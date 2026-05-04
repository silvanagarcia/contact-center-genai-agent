"""
Lambda de autenticacion de proveedores — Sigma Foodservice POC
Invocada desde el Amazon Connect Contact Flow antes de llegar al bot de Lex.

Flujo:
  1. Connect envia el numero de telefono del proveedor (formato E.164)
  2. Esta Lambda busca el numero en DynamoDB (GSI phone-index)
  3. Si encuentra el proveedor: retorna sus datos como Contact Attributes
  4. Si no encuentra: retorna modo anonimo (el bot puede ayudar con FAQs generales)
  5. Los Contact Attributes son visibles para el agente en el Agent Workspace

En produccion: el lookup seria contra Amazon Connect Customer Profiles,
que se alimenta de Snowflake via S3 + ingesta programada.
"""

import json
import logging
import os
import time
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

# Variables de entorno
PROVIDERS_TABLE = os.environ['PROVIDERS_TABLE']
SESSIONS_TABLE = os.environ['SESSIONS_TABLE']
PHONE_GSI = os.environ.get('PHONE_GSI', 'phone-index')

dynamodb = boto3.resource('dynamodb')
providers_table = dynamodb.Table(PROVIDERS_TABLE)
sessions_table = dynamodb.Table(SESSIONS_TABLE)


def lambda_handler(event, context):
    """
    Handler principal invocado por Amazon Connect.

    Evento de entrada (Connect):
    {
        "Details": {
            "ContactData": {
                "ContactId": "abc-123",
                "CustomerEndpoint": { "Address": "+521234567890" },
                "Channel": "CHAT",
                ...
            },
            "Parameters": {}
        },
        "Name": "ContactFlowEvent"
    }

    Respuesta esperada por Connect: dict con los Contact Attributes a agregar.
    """
    logger.info(f'Auth Lambda invocada - event keys: {list(event.keys())}')

    try:
        contact_data = event.get('Details', {}).get('ContactData', {})
        contact_id = contact_data.get('ContactId', 'unknown')
        customer_endpoint = contact_data.get('CustomerEndpoint', {})
        phone = customer_endpoint.get('Address', '')

        logger.info(f'ContactId: {contact_id} | Phone: {mask_phone(phone)}')

        # Buscar en sesion activa primero (evitar re-autenticar en la misma sesion)
        cached = get_cached_session(contact_id)
        if cached:
            logger.info(f'Sesion activa encontrada para contactId: {contact_id}')
            return cached

        # Lookup del proveedor por telefono
        provider = lookup_provider_by_phone(phone)

        if provider:
            logger.info(f'Proveedor autenticado: {provider.get("providerId")} - {provider.get("name")}')
            attributes = build_authenticated_attributes(provider)
        else:
            logger.info(f'Proveedor no encontrado para telefono: {mask_phone(phone)} - modo anonimo')
            attributes = build_anonymous_attributes()

        # Guardar en sesion para este contactId
        save_session(contact_id, attributes)

        logger.info(f'Atributos retornados: authenticated={attributes.get("authenticated")}')
        return attributes

    except Exception as e:
        logger.error(f'Error en autenticacion: {str(e)}', exc_info=True)
        # En caso de error retornamos modo anonimo para no bloquear la atencion
        return build_anonymous_attributes()


def lookup_provider_by_phone(phone: str) -> dict | None:
    """Busca el proveedor en DynamoDB usando el GSI de telefono."""
    if not phone:
        return None

    # Normalizar formato E.164 (Connect siempre envia con +)
    phone = normalize_phone(phone)

    try:
        response = providers_table.query(
            IndexName=PHONE_GSI,
            KeyConditionExpression=Key('phone').eq(phone),
            Limit=1
        )
        items = response.get('Items', [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f'Error consultando DynamoDB por telefono: {str(e)}')
        return None


def build_authenticated_attributes(provider: dict) -> dict:
    """
    Construye los Contact Attributes para un proveedor autenticado.
    Estos atributos son visibles en el Agent Workspace de Connect.
    """
    # Determinar si la cuenta tiene algun problema
    credit_status = provider.get('creditStatus', 'ACTIVO')
    is_blocked = credit_status in ('BLOQUEADO', 'SUSPENDIDO')

    return {
        # Flag de autenticacion
        'authenticated': 'true',

        # Datos de identificacion del proveedor
        'providerId':    str(provider.get('providerId', '')),
        'providerName':  str(provider.get('name', '')),
        'sucursal':      str(provider.get('sucursal', '')),

        # Estado de cuenta (para el bot y el agente)
        'creditStatus':  credit_status,
        'isBlocked':     'true' if is_blocked else 'false',
        'creditLimit':   str(provider.get('creditLimit', '')),
        'balance':       str(provider.get('balance', '')),

        # Datos de pedidos
        'lastOrderDate':         str(provider.get('lastOrderDate', '')),
        'orderFrequencyDays':    str(provider.get('orderFrequencyDays', '')),

        # Opt-in WhatsApp
        'whatsappOptIn':  'true' if provider.get('optIn') else 'false',

        # Resumen para el agente (visible en Agent Workspace)
        'providerSummary': build_agent_summary(provider),
    }


def build_anonymous_attributes() -> dict:
    """
    Atributos para proveedor no identificado.
    El bot puede responder FAQs generales pero no datos personalizados.
    """
    return {
        'authenticated':    'false',
        'providerId':       '',
        'providerName':     '',
        'sucursal':         '',
        'creditStatus':     '',
        'isBlocked':        'false',
        'creditLimit':      '',
        'balance':          '',
        'lastOrderDate':    '',
        'orderFrequencyDays': '',
        'whatsappOptIn':    'false',
        'providerSummary':  'Proveedor no identificado - atencion sin autenticacion',
    }


def build_agent_summary(provider: dict) -> str:
    """Resumen del proveedor que ve el agente en el Agent Workspace al recibir el escalamiento."""
    name = provider.get('name', 'Desconocido')
    status = provider.get('creditStatus', 'N/A')
    last_order = provider.get('lastOrderDate', 'N/A')
    sucursal = provider.get('sucursal', 'N/A')
    return f'Proveedor: {name} | Sucursal: {sucursal} | Credito: {status} | Ultimo pedido: {last_order}'


def get_cached_session(contact_id: str) -> dict | None:
    """Intenta recuperar atributos de una sesion activa para este contactId."""
    try:
        response = sessions_table.get_item(Key={'contactId': contact_id})
        item = response.get('Item')
        if item and item.get('attributes'):
            return item['attributes']
        return None
    except Exception as e:
        logger.warning(f'Error recuperando sesion: {str(e)}')
        return None


def save_session(contact_id: str, attributes: dict):
    """Guarda los atributos de autenticacion en la tabla de sesiones (TTL: 8 horas)."""
    try:
        ttl = int(time.time()) + (8 * 3600)  # 8 horas
        sessions_table.put_item(Item={
            'contactId': contact_id,
            'attributes': attributes,
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'ttl': ttl,
        })
    except Exception as e:
        logger.warning(f'Error guardando sesion (no critico): {str(e)}')


def normalize_phone(phone: str) -> str:
    """Normaliza el numero de telefono a formato E.164 (+521234567890)."""
    phone = phone.strip()
    # Si no tiene +, agregar
    if not phone.startswith('+'):
        # Asumir Mexico si tiene 10 digitos
        digits = ''.join(filter(str.isdigit, phone))
        if len(digits) == 10:
            phone = '+52' + digits
        elif len(digits) == 12 and digits.startswith('52'):
            phone = '+' + digits
        else:
            phone = '+' + digits
    return phone


def mask_phone(phone: str) -> str:
    """Oculta los ultimos 4 digitos del telefono para logs (privacidad)."""
    if len(phone) > 4:
        return phone[:-4] + '****'
    return '****'
