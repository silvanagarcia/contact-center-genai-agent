"""
Lambda de mensajes proactivos — Sigma Foodservice POC
Invocada por EventBridge de lunes a viernes a las 8:00 AM hora Mexico.

Tipos de mensajes:
  A) Recordatorio de pedido: proveedores que no han pedido en X dias
  B) Aviso de bloqueo: proveedores con creditStatus = BLOQUEADO o SUSPENDIDO

Flujo:
  1. Escanear DynamoDB para obtener todos los proveedores activos
  2. Aplicar filtros de elegibilidad (optIn, hasWhatsApp, ventana horaria, reintentos)
  3. Clasificar: recordatorio vs bloqueo
  4. Registrar en tabla de log (evitar duplicados y controlar reintentos)
  5. Disparar via Amazon Connect Outbound Campaigns (o social-messaging API en POC)

En produccion: el scan de DynamoDB se reemplaza por una consulta a Snowflake
(via COPY INTO S3 + Lambda con snowflake-connector-python).
La logica de filtros y despacho permanece igual.
"""

import json
import logging
import os
import time
import boto3
from boto3.dynamodb.conditions import Key, Attr
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

# Variables de entorno
PROVIDERS_TABLE          = os.environ['PROVIDERS_TABLE']
PROACTIVE_LOG_TABLE      = os.environ['PROACTIVE_LOG_TABLE']
CONNECT_INSTANCE_ARN     = os.environ['CONNECT_INSTANCE_ARN']
RECORDATORIO_CAMPAIGN_ID = os.environ.get('RECORDATORIO_CAMPAIGN_ID', 'PENDING')
BLOQUEO_CAMPAIGN_ID      = os.environ.get('BLOQUEO_CAMPAIGN_ID', 'PENDING')
DAYS_THRESHOLD           = int(os.environ.get('DAYS_THRESHOLD', '14'))
MAX_DAILY_MESSAGES       = int(os.environ.get('MAX_DAILY_MESSAGES', '1'))
SEND_WINDOW_START        = int(os.environ.get('SEND_WINDOW_START', '8'))
SEND_WINDOW_END          = int(os.environ.get('SEND_WINDOW_END', '17'))
TIMEZONE                 = os.environ.get('TIMEZONE', 'America/Mexico_City')

dynamodb = boto3.resource('dynamodb')
providers_table    = dynamodb.Table(PROVIDERS_TABLE)
proactive_log_table = dynamodb.Table(PROACTIVE_LOG_TABLE)

connect_campaigns  = boto3.client('connectcampaignsv2')
social_messaging   = boto3.client('socialmessaging')


def lambda_handler(event, context):
    """
    Handler principal invocado por EventBridge.
    Retorna un resumen de mensajes enviados para CloudWatch Logs.
    """
    logger.info(f'Inicio proceso proactivos - {datetime.now(timezone.utc).isoformat()}')

    # Verificar ventana horaria
    local_now = datetime.now(ZoneInfo(TIMEZONE))
    if not is_within_send_window(local_now):
        logger.info(f'Fuera de ventana de envio ({SEND_WINDOW_START}-{SEND_WINDOW_END}h {TIMEZONE}). Hora actual: {local_now.strftime("%H:%M")}')
        return {'status': 'skipped', 'reason': 'outside_send_window'}

    today_str = local_now.strftime('%Y-%m-%d')

    # Obtener todos los proveedores activos
    providers = scan_eligible_providers()
    logger.info(f'Proveedores activos escaneados: {len(providers)}')

    stats = {
        'total_scanned': len(providers),
        'recordatorio_sent': 0,
        'bloqueo_sent': 0,
        'skipped_no_optin': 0,
        'skipped_already_sent': 0,
        'skipped_no_phone': 0,
        'errors': 0,
    }

    for provider in providers:
        provider_id = provider.get('providerId')
        try:
            result = process_provider(provider, today_str)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            logger.error(f'Error procesando proveedor {provider_id}: {str(e)}', exc_info=True)
            stats['errors'] += 1

    logger.info(f'Proceso completado: {json.dumps(stats)}')
    return {'status': 'completed', 'stats': stats, 'date': today_str}


def process_provider(provider: dict, today_str: str) -> str:
    """
    Evalua si un proveedor debe recibir un mensaje proactivo hoy.
    Retorna una clave de stats indicando que paso con este proveedor.
    """
    provider_id    = provider.get('providerId')
    phone          = provider.get('phone', '')
    has_whatsapp   = provider.get('hasWhatsApp', False)
    opt_in         = provider.get('optIn', False)
    credit_status  = provider.get('creditStatus', 'ACTIVO')
    last_order_str = provider.get('lastOrderDate', '')

    # Filtros de elegibilidad
    if not phone:
        return 'skipped_no_phone'

    if not opt_in or not has_whatsapp:
        return 'skipped_no_optin'

    # Verificar si ya recibio un mensaje hoy (control de duplicados)
    if already_sent_today(provider_id, today_str):
        return 'skipped_already_sent'

    # Clasificar tipo de mensaje
    message_type = classify_message_type(credit_status, last_order_str)

    if message_type is None:
        return 'skipped_no_action_needed'

    # Enviar mensaje
    sent = send_proactive_message(provider, message_type)

    if sent:
        # Registrar en log para evitar duplicados
        log_message_sent(provider_id, today_str, message_type)
        return f'{message_type}_sent'
    else:
        return 'errors'


def classify_message_type(credit_status: str, last_order_str: str) -> str | None:
    """
    Determina el tipo de mensaje proactivo a enviar, o None si no aplica.
    Prioridad: bloqueo > recordatorio de pedido.
    """
    # Tipo B: Aviso de bloqueo (maxima prioridad)
    if credit_status in ('BLOQUEADO', 'SUSPENDIDO'):
        return 'bloqueo'

    # Tipo A: Recordatorio por frecuencia de compra
    if last_order_str:
        try:
            last_order = datetime.strptime(last_order_str, '%Y-%m-%d').date()
            days_since = (datetime.now(ZoneInfo(TIMEZONE)).date() - last_order).days
            if days_since >= DAYS_THRESHOLD:
                return 'recordatorio'
        except ValueError:
            logger.warning(f'Formato de fecha invalido: {last_order_str}')

    return None


def send_proactive_message(provider: dict, message_type: str) -> bool:
    """
    Envia el mensaje proactivo via Connect Outbound Campaigns.
    En POC, si los IDs de campana estan en PENDING, solo loguea (modo simulacion).
    """
    provider_id = provider.get('providerId')
    phone       = provider.get('phone')
    name        = provider.get('name', 'proveedor')

    campaign_id = RECORDATORIO_CAMPAIGN_ID if message_type == 'recordatorio' else BLOQUEO_CAMPAIGN_ID

    # Modo simulacion: si la campana no esta configurada, solo loguea
    if campaign_id == 'PENDING':
        logger.info(
            f'[SIMULACION] Mensaje {message_type} para {provider_id} ({name}) '
            f'al telefono {mask_phone(phone)} - Campana no configurada aun'
        )
        return True  # Simular exito para que el log se registre

    # Modo real: disparar via Connect Outbound Campaigns v2
    try:
        logger.info(f'Enviando {message_type} a proveedor {provider_id} - campana {campaign_id}')

        connect_campaigns.put_dial_request_batch(
            campaignId=campaign_id,
            dialRequests=[{
                'clientToken':  f'{provider_id}-{message_type}-{int(time.time())}',
                'phoneNumber':  phone,
                'expirationTime': (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
                'attributes': {
                    'providerName': name,
                    'messageType':  message_type,
                    'providerId':   provider_id,
                }
            }]
        )
        logger.info(f'Mensaje {message_type} encolado exitosamente para {provider_id}')
        return True

    except Exception as e:
        logger.error(f'Error enviando mensaje {message_type} a {provider_id}: {str(e)}')
        return False


def scan_eligible_providers() -> list[dict]:
    """
    Escanea DynamoDB para obtener todos los proveedores activos.
    En produccion: reemplazar con consulta a Snowflake.
    """
    try:
        response = providers_table.scan(
            FilterExpression=Attr('optIn').eq(True) & Attr('hasWhatsApp').eq(True)
        )
        items = response.get('Items', [])

        # Manejar paginacion
        while 'LastEvaluatedKey' in response:
            response = providers_table.scan(
                FilterExpression=Attr('optIn').eq(True) & Attr('hasWhatsApp').eq(True),
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items.extend(response.get('Items', []))

        return items
    except Exception as e:
        logger.error(f'Error escaneando proveedores: {str(e)}')
        return []


def already_sent_today(provider_id: str, today_str: str) -> bool:
    """Verifica si ya se envio un mensaje proactivo a este proveedor hoy."""
    try:
        response = proactive_log_table.get_item(
            Key={'providerId': provider_id, 'campaignDate': today_str}
        )
        return 'Item' in response
    except Exception as e:
        logger.warning(f'Error verificando log de proactivos: {str(e)}')
        return False  # En caso de duda, permitir el envio


def log_message_sent(provider_id: str, today_str: str, message_type: str):
    """Registra en DynamoDB que se envio un mensaje proactivo hoy."""
    try:
        ttl = int(time.time()) + (7 * 24 * 3600)  # TTL: 7 dias
        proactive_log_table.put_item(Item={
            'providerId':    provider_id,
            'campaignDate':  today_str,
            'messageType':   message_type,
            'sentAt':        datetime.now(timezone.utc).isoformat(),
            'ttl':           ttl,
        })
    except Exception as e:
        logger.warning(f'Error registrando log de proactivo (no critico): {str(e)}')


def is_within_send_window(local_now: datetime) -> bool:
    """Verifica si estamos dentro de la ventana horaria de envio."""
    # No enviar en fin de semana (6=sabado, 7=domingo segun isoweekday)
    if local_now.isoweekday() in (6, 7):
        return False
    return SEND_WINDOW_START <= local_now.hour < SEND_WINDOW_END


def mask_phone(phone: str) -> str:
    """Oculta los ultimos 4 digitos del telefono para logs."""
    if len(phone) > 4:
        return phone[:-4] + '****'
    return '****'
