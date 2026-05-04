"""
Script de seed — Carga proveedores ficticios en DynamoDB (mock Snowflake)
Sigma Foodservice POC

Uso:
  python3 seed_providers.py --table sigma-providers-poc --region us-east-1
  python3 seed_providers.py --table sigma-providers-poc --region us-east-1 --delete

Descripcion de los 15 proveedores de prueba:
  - 5 activos con pedidos recientes      → NO necesitan proactivo
  - 5 activos con pedidos vencidos       → TRIGGER recordatorio de pedido
  - 3 bloqueados / suspendidos           → TRIGGER aviso de bloqueo
  - 2 sin opt-in o sin WhatsApp          → EXCLUIDOS de campanas proactivas

Esto permite probar todos los escenarios del bot y de los mensajes proactivos.
"""

import json
import boto3
import argparse
import sys
import os
from decimal import Decimal
from datetime import datetime

def parse_args():
    parser = argparse.ArgumentParser(description='Seed de proveedores Sigma POC en DynamoDB')
    parser.add_argument('--table',  required=True, help='Nombre de la tabla DynamoDB')
    parser.add_argument('--region', default='us-east-1', help='Region AWS')
    parser.add_argument('--profile', default=None, help='Perfil AWS CLI (opcional)')
    parser.add_argument('--delete', action='store_true', help='Eliminar todos los items antes de insertar')
    parser.add_argument('--dry-run', action='store_true', help='Mostrar lo que se haria sin ejecutar')
    return parser.parse_args()


def load_providers_data() -> list[dict]:
    """Carga los datos de proveedores desde el archivo JSON del mismo directorio."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_file  = os.path.join(script_dir, 'providers_data.json')

    if not os.path.exists(data_file):
        print(f'ERROR: No se encontro el archivo {data_file}')
        sys.exit(1)

    with open(data_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def convert_to_dynamodb_types(item: dict) -> dict:
    """
    Convierte los tipos de Python a tipos compatibles con DynamoDB.
    DynamoDB no acepta float nativo — usar Decimal para numeros.
    """
    converted = {}
    for key, value in item.items():
        if value is None:
            continue  # DynamoDB no acepta None/null — omitir el campo
        elif isinstance(value, float):
            converted[key] = Decimal(str(value))
        elif isinstance(value, int):
            converted[key] = Decimal(str(value))
        elif isinstance(value, bool):
            converted[key] = value
        else:
            converted[key] = value
    return converted


def delete_all_items(table):
    """Elimina todos los items de la tabla (para re-seed limpio)."""
    print('Eliminando items existentes...')
    scan = table.scan()
    items = scan.get('Items', [])

    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={'providerId': item['providerId']})

    while 'LastEvaluatedKey' in scan:
        scan = table.scan(ExclusiveStartKey=scan['LastEvaluatedKey'])
        items = scan.get('Items', [])
        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={'providerId': item['providerId']})

    print(f'  Eliminados {len(items)} items')


def seed_providers(table, providers: list[dict], dry_run: bool = False):
    """Inserta los proveedores en DynamoDB usando batch_writer."""
    print(f'Insertando {len(providers)} proveedores{"  [DRY RUN]" if dry_run else ""}...')

    success = 0
    errors  = 0

    if not dry_run:
        with table.batch_writer() as batch:
            for provider in providers:
                try:
                    item = convert_to_dynamodb_types(provider)
                    # Agregar timestamp de creacion
                    item['seededAt'] = datetime.utcnow().isoformat() + 'Z'
                    batch.put_item(Item=item)
                    success += 1
                    print(f'  OK  {provider["providerId"]:20s} | {provider["name"][:35]:35s} | {provider["creditStatus"]:10s} | {provider.get("comment", "")[:50]}')
                except Exception as e:
                    errors += 1
                    print(f'  ERR {provider.get("providerId", "?"):20s} | {str(e)}')
    else:
        for provider in providers:
            status_icon = '✅' if provider.get('optIn') and provider.get('hasWhatsApp') else '⚠️'
            print(f'  {status_icon} {provider["providerId"]:20s} | {provider["name"][:35]:35s} | {provider["creditStatus"]:10s}')
            success += 1

    return success, errors


def print_summary(providers: list[dict]):
    """Imprime un resumen de los escenarios de prueba en los datos."""
    print('\n' + '='*70)
    print('RESUMEN DE ESCENARIOS DE PRUEBA')
    print('='*70)

    scenarios = {
        'Activos sin proactivo':      [p for p in providers if p['creditStatus'] == 'ACTIVO' and p.get('optIn') and p.get('hasWhatsApp') and p.get('comment', '').startswith('Pedido reciente')],
        'Recordatorio de pedido':     [p for p in providers if 'TRIGGER recordatorio' in p.get('comment', '')],
        'Aviso de bloqueo':           [p for p in providers if 'TRIGGER aviso bloqueo' in p.get('comment', '')],
        'Sin WhatsApp o sin opt-in':  [p for p in providers if not p.get('optIn') or not p.get('hasWhatsApp')],
    }

    for scenario, items in scenarios.items():
        print(f'\n  {scenario} ({len(items)}):')
        for p in items:
            phone_display = p['phone'][-4:].rjust(20, ' ')
            print(f'    - {p["providerId"]:20s} | {p["name"][:35]:35s} | ...{phone_display}')

    print('\n' + '='*70)
    print(f'Total: {len(providers)} proveedores')
    print('Para probar autenticacion, usa cualquiera de estos telefonos en el Web Chat Widget')
    print('='*70 + '\n')


def main():
    args = parse_args()

    # Configurar sesion AWS
    session_kwargs = {'region_name': args.region}
    if args.profile:
        session_kwargs['profile_name'] = args.profile

    session  = boto3.Session(**session_kwargs)
    dynamodb = session.resource('dynamodb')
    table    = dynamodb.Table(args.table)

    print(f'\nSigma Foodservice POC — Seed de proveedores')
    print(f'Tabla:  {args.table}')
    print(f'Region: {args.region}')
    print(f'DryRun: {args.dry_run}')
    print('-'*50)

    # Cargar datos
    providers = load_providers_data()
    print_summary(providers)

    if args.dry_run:
        print('[DRY RUN] No se realizaron cambios en DynamoDB.\n')
        return

    # Verificar que la tabla existe
    try:
        table.load()
    except Exception as e:
        print(f'ERROR: No se puede acceder a la tabla "{args.table}": {str(e)}')
        print('Verificar que la tabla existe y que tienes los permisos necesarios.')
        sys.exit(1)

    # Eliminar items existentes si se pide
    if args.delete:
        delete_all_items(table)

    # Insertar proveedores
    success, errors = seed_providers(table, providers, dry_run=False)

    print(f'\nResultado: {success} insertados, {errors} errores')

    if errors > 0:
        print('ADVERTENCIA: Algunos items no se insertaron correctamente.')
        sys.exit(1)
    else:
        print('Seed completado exitosamente.')


if __name__ == '__main__':
    main()
