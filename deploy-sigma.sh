#!/bin/bash
# =============================================================================
# deploy-sigma.sh — Script de despliegue POC Sigma Foodservice
# =============================================================================
# Despliega en orden todos los stacks de CloudFormation necesarios para la POC.
# El orden importa: cada stack puede depender de outputs del anterior.
#
# Uso:
#   ./deploy-sigma.sh \
#     --bucket mi-artifacts-bucket \
#     --connect-arn arn:aws:connect:us-east-1:123456789:instance/xxxx \
#     --content-bucket mi-kb-content-bucket \
#     --region us-east-1
#
# Para solo desplegar un stack especifico:
#   ./deploy-sigma.sh --stack db --bucket ... --region us-east-1
#
# Stacks disponibles:
#   base-kb       Stack 1: Bedrock Knowledge Base + OpenSearch
#   base-rag      Stack 3: Lex Bot + Lambda + Contact Flow
#   sigma-db      DynamoDB (mock Snowflake)
#   sigma-auth    Lambda de autenticacion
#   sigma-proact  Lambda de mensajes proactivos + EventBridge
#   sigma-wa      Integracion WhatsApp (requiere Embedded Sign-Up previo)
#   seed          Seed de proveedores ficticios en DynamoDB
#   content       Carga de FAQs y catalogo al bucket de la KB
#   all           Todo en orden (default)
# =============================================================================

set -euo pipefail

# ─── Colores para output ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_success() { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ─── Parametros con defaults ──────────────────────────────────────────────────
ARTIFACTS_BUCKET=""
CONNECT_INSTANCE_ARN=""
CONTENT_BUCKET=""
AWS_REGION="us-east-1"
ENVIRONMENT="poc"
STACK_TARGET="all"

# WhatsApp (opcionales hasta que se complete el Embedded Sign-Up)
WA_PHONE_NUMBER_ID="PENDING_EMBEDDED_SIGNUP"
WA_WABA_ID="PENDING_EMBEDDED_SIGNUP"

# IDs de campanas (se completan post-deploy de sigma-proact)
RECORDATORIO_CAMPAIGN_ID="PENDING"
BLOQUEO_CAMPAIGN_ID="PENDING"

# ─── Parsear argumentos ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --bucket)          ARTIFACTS_BUCKET="$2"; shift 2 ;;
        --connect-arn)     CONNECT_INSTANCE_ARN="$2"; shift 2 ;;
        --content-bucket)  CONTENT_BUCKET="$2"; shift 2 ;;
        --region)          AWS_REGION="$2"; shift 2 ;;
        --env)             ENVIRONMENT="$2"; shift 2 ;;
        --stack)           STACK_TARGET="$2"; shift 2 ;;
        --wa-phone-id)     WA_PHONE_NUMBER_ID="$2"; shift 2 ;;
        --wa-waba-id)      WA_WABA_ID="$2"; shift 2 ;;
        --recordatorio-id) RECORDATORIO_CAMPAIGN_ID="$2"; shift 2 ;;
        --bloqueo-id)      BLOQUEO_CAMPAIGN_ID="$2"; shift 2 ;;
        --help|-h)         grep '^#' "$0" | cut -c3-; exit 0 ;;
        *)                 log_error "Argumento desconocido: $1" ;;
    esac
done

# ─── Validaciones ─────────────────────────────────────────────────────────────
[[ -z "$ARTIFACTS_BUCKET" ]]       && log_error "--bucket es requerido"
[[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "base-kb" || "$STACK_TARGET" == "base-rag" ]] \
    && [[ -z "$CONNECT_INSTANCE_ARN" ]] && log_error "--connect-arn es requerido para este stack"

CONTENT_BUCKET="${CONTENT_BUCKET:-$ARTIFACTS_BUCKET}"

# ─── Funciones de utilidad ───────────────────────────────────────────────────

# Construir y subir artefactos Lambda de Sigma al bucket de S3
publish_sigma_artifacts() {
    log_info "Publicando artefactos Lambda de Sigma en S3..."

    for lambda_dir in auth proactive-messages; do
        src_path="src/sigma/${lambda_dir}"
        if [[ -d "$src_path" ]]; then
            log_info "  Empaquetando ${lambda_dir}..."
            zip_file="/tmp/sigma-${lambda_dir}-handler.zip"
            (cd "$src_path" && zip -r "$zip_file" . -x "*.pyc" -x "__pycache__/*") > /dev/null
            aws s3 cp "$zip_file" "s3://${ARTIFACTS_BUCKET}/sigma/${lambda_dir}/handler.zip" \
                --region "$AWS_REGION"
            log_success "  ${lambda_dir} publicado"
        fi
    done
}

# Desplegar un stack de CloudFormation con reintentos
deploy_stack() {
    local stack_name="$1"
    local template="$2"
    shift 2
    local params=("$@")

    log_info "Desplegando stack: ${stack_name}"

    # Construir string de parametros para AWS CLI
    local params_str=""
    for param in "${params[@]}"; do
        params_str+="ParameterKey=${param%%=*},ParameterValue=${param#*=} "
    done

    aws cloudformation deploy \
        --stack-name "$stack_name" \
        --template-file "$template" \
        --parameter-overrides $params_str \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION" \
        --no-fail-on-empty-changeset

    log_success "Stack ${stack_name} desplegado"
}

# Obtener output de un stack de CloudFormation
get_stack_output() {
    local stack_name="$1"
    local output_key="$2"
    aws cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
        --output text 2>/dev/null || echo ""
}

# ─── HEADER ───────────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo "  Sigma Foodservice POC — Deploy Script"
echo "================================================================="
echo "  Ambiente:  $ENVIRONMENT"
echo "  Region:    $AWS_REGION"
echo "  Bucket:    $ARTIFACTS_BUCKET"
echo "  Stack:     $STACK_TARGET"
echo "================================================================="
echo ""

# ─── STACKS ───────────────────────────────────────────────────────────────────

# 1. Publicar artefactos (siempre, excepto solo seed o content)
if [[ "$STACK_TARGET" != "seed" && "$STACK_TARGET" != "content" ]]; then
    log_info "Publicando artefactos en S3..."
    # Artefactos del repo base (hotel-bot-handler = ahora sigma-bot-handler)
    if [[ -f "src/publish-all.sh" ]]; then
        cd src && bash publish-all.sh "$ARTIFACTS_BUCKET" && cd ..
    fi
    # Artefactos de Sigma
    publish_sigma_artifacts
fi

# ─── Stack: Bedrock Knowledge Base ───────────────────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "base-kb" ]]; then
    deploy_stack "sigma-${ENVIRONMENT}-knowledge-base" \
        "infrastructure/bedrock-KB.yaml" \
        "S3BucketName=${CONTENT_BUCKET}" \
        "EmbeddingModelArn=arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0" \
        "ChunkingStrategy=FIXED_SIZE" \
        "MaxTokens=600" \
        "OverlapPercentage=10" \
        "CloudFormationBucket=${ARTIFACTS_BUCKET}"

    KB_ID=$(get_stack_output "sigma-${ENVIRONMENT}-knowledge-base" "KnowledgeBaseId")
    log_info "Knowledge Base ID: ${KB_ID}"
fi

# ─── Stack: RAG Solution (Lex Bot + Lambda + Contact Flow) ───────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "base-rag" ]]; then
    [[ -z "$KB_ID" ]] && KB_ID=$(get_stack_output "sigma-${ENVIRONMENT}-knowledge-base" "KnowledgeBaseId")
    [[ -z "$KB_ID" ]] && log_error "No se pudo obtener el KnowledgeBaseId. Desplegar base-kb primero."

    deploy_stack "sigma-${ENVIRONMENT}-rag-solution" \
        "infrastructure/contact-center-RAG-solution.yaml" \
        "LexBotName=sigma-${ENVIRONMENT}-bot" \
        "ConversationTurns=4" \
        "ConnectInstanceArn=${CONNECT_INSTANCE_ARN}" \
        "ContactFlowName=Sigma POC Contact Flow" \
        "KnowledgeBaseId=${KB_ID}" \
        "KnowledgeBaseS3Bucket=${CONTENT_BUCKET}" \
        "CloudFormationBucket=${ARTIFACTS_BUCKET}"

    CONTACT_FLOW_ID=$(get_stack_output "sigma-${ENVIRONMENT}-rag-solution" "ContactFlowId")
    log_info "Contact Flow ID: ${CONTACT_FLOW_ID}"
fi

# ─── Stack: DynamoDB (mock Snowflake) ────────────────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "sigma-db" ]]; then
    deploy_stack "sigma-${ENVIRONMENT}-providers-db" \
        "infrastructure/sigma/sigma-providers-db.yaml" \
        "EnvironmentName=${ENVIRONMENT}"
fi

# ─── Stack: Lambda de autenticacion ──────────────────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "sigma-auth" ]]; then
    deploy_stack "sigma-${ENVIRONMENT}-customer-auth" \
        "infrastructure/sigma/sigma-customer-auth.yaml" \
        "EnvironmentName=${ENVIRONMENT}" \
        "ProvidersTableName=sigma-providers-${ENVIRONMENT}" \
        "SessionsTableName=sigma-sessions-${ENVIRONMENT}" \
        "ArtifactsBucket=${ARTIFACTS_BUCKET}"

    AUTH_LAMBDA_ARN=$(get_stack_output "sigma-${ENVIRONMENT}-customer-auth" "AuthLambdaArn")
    log_success "Auth Lambda ARN: ${AUTH_LAMBDA_ARN}"
    log_warning "ACCION MANUAL: Agregar esta Lambda al Contact Flow de Connect:"
    log_warning "  ${AUTH_LAMBDA_ARN}"
fi

# ─── Stack: Mensajes proactivos ───────────────────────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "sigma-proact" ]]; then
    deploy_stack "sigma-${ENVIRONMENT}-proactive-messages" \
        "infrastructure/sigma/sigma-proactive-messages.yaml" \
        "EnvironmentName=${ENVIRONMENT}" \
        "ProvidersTableName=sigma-providers-${ENVIRONMENT}" \
        "ProactiveLogTableName=sigma-proactive-log-${ENVIRONMENT}" \
        "ConnectInstanceArn=${CONNECT_INSTANCE_ARN}" \
        "RecordatorioCampaignId=${RECORDATORIO_CAMPAIGN_ID}" \
        "BloqueoCampaignId=${BLOQUEO_CAMPAIGN_ID}" \
        "ArtifactsBucket=${ARTIFACTS_BUCKET}"

    log_warning "ACCIONES MANUALES para completar mensajes proactivos:"
    log_warning "  1. Crear campana 'Recordatorio de pedido' en Connect Console -> Outbound Campaigns"
    log_warning "  2. Crear campana 'Aviso de bloqueo' en Connect Console -> Outbound Campaigns"
    log_warning "  3. Re-ejecutar con: --recordatorio-id <id> --bloqueo-id <id>"
fi

# ─── Stack: WhatsApp ──────────────────────────────────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "sigma-wa" ]]; then
    [[ -z "$CONTACT_FLOW_ID" ]] && CONTACT_FLOW_ID=$(get_stack_output "sigma-${ENVIRONMENT}-rag-solution" "ContactFlowId")

    deploy_stack "sigma-${ENVIRONMENT}-whatsapp" \
        "infrastructure/sigma/sigma-whatsapp.yaml" \
        "EnvironmentName=${ENVIRONMENT}" \
        "ConnectInstanceArn=${CONNECT_INSTANCE_ARN}" \
        "ConnectContactFlowId=${CONTACT_FLOW_ID}" \
        "WhatsAppPhoneNumberId=${WA_PHONE_NUMBER_ID}" \
        "WhatsAppWABAId=${WA_WABA_ID}"

    if [[ "$WA_PHONE_NUMBER_ID" == "PENDING_EMBEDDED_SIGNUP" ]]; then
        log_warning "WhatsApp PENDIENTE — Completar Embedded Sign-Up en AWS Console:"
        log_warning "  AWS Console -> Amazon Connect -> Canales -> WhatsApp"
        log_warning "  Luego re-ejecutar con: --wa-phone-id <id> --wa-waba-id <id>"
        log_info "  Mientras tanto, usar el Web Chat Widget para probar el bot"
    fi
fi

# ─── Seed de proveedores ──────────────────────────────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "seed" ]]; then
    log_info "Cargando proveedores ficticios en DynamoDB..."
    python3 src/sigma/seed/seed_providers.py \
        --table "sigma-providers-${ENVIRONMENT}" \
        --region "$AWS_REGION"
    log_success "Seed completado"
fi

# ─── Carga de contenido a S3 (FAQs + catalogo) ───────────────────────────────
if [[ "$STACK_TARGET" == "all" || "$STACK_TARGET" == "content" ]]; then
    log_info "Subiendo contenido de FAQs y catalogo a S3..."
    aws s3 sync content/sigma/ "s3://${CONTENT_BUCKET}/sigma/" \
        --region "$AWS_REGION" \
        --delete
    log_success "Contenido subido a s3://${CONTENT_BUCKET}/sigma/"
    log_warning "ACCION MANUAL: Sincronizar la Knowledge Base en Bedrock Console:"
    log_warning "  AWS Console -> Bedrock -> Knowledge Bases -> sigma-${ENVIRONMENT}-kb -> Sync"
fi

# ─── RESUMEN FINAL ────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo -e "  ${GREEN}Deploy completado!${NC}"
echo "================================================================="
echo ""
echo "  PROXIMOS PASOS:"
echo ""
echo "  1. Web Chat Widget (probar el bot YA):"
echo "     AWS Console -> Connect -> Canales -> Chat -> Crear widget"
echo "     Seleccionar Contact Flow: 'Sigma POC Contact Flow'"
echo ""
echo "  2. Agregar Lambda de autenticacion al Contact Flow:"
echo "     Abrir Contact Flow en el editor visual de Connect"
echo "     Agregar bloque 'Invoke AWS Lambda' antes del bloque de Lex"
echo "     Seleccionar: sigma-auth-handler-${ENVIRONMENT}"
echo ""
echo "  3. Sincronizar Knowledge Base:"
echo "     AWS Console -> Bedrock -> Knowledge Bases -> Sync"
echo ""
echo "  4. WhatsApp (cuando este listo el Embedded Sign-Up):"
echo "     Re-ejecutar: ./deploy-sigma.sh --stack sigma-wa --wa-phone-id <id> --wa-waba-id <id>"
echo ""
echo "  5. Mensajes proactivos (cuando esten las campanas):"
echo "     Re-ejecutar: ./deploy-sigma.sh --stack sigma-proact --recordatorio-id <id> --bloqueo-id <id>"
echo ""
echo "================================================================="
