# Contact Center GenAI Agent — Sigma Foodservice POC

> **Fork del proyecto [aws-samples/contact-center-genai-agent](https://github.com/aws-samples/contact-center-genai-agent)**  
> Adaptado por Nubity para la POC de Sigma Alimentos Foodservice.

---

## Índice

- [POC Sigma Foodservice](#poc-sigma-foodservice)
  - [Qué se construyó](#qué-se-construyó)
  - [Arquitectura](#arquitectura)
  - [Recursos desplegados](#recursos-desplegados)
  - [Cómo probar ahora (Web Chat Widget)](#cómo-probar-ahora-web-chat-widget)
  - [Proveedores de prueba](#proveedores-de-prueba)
  - [Escenarios de prueba](#escenarios-de-prueba)
  - [Intents del bot](#intents-del-bot)
  - [Pasos manuales pendientes](#pasos-manuales-pendientes)
  - [Re-deploy y mantenimiento](#re-deploy-y-mantenimiento)
  - [Estructura de archivos Sigma](#estructura-de-archivos-sigma)
- [Proyecto base AWS (referencia)](#proyecto-base-aws-referencia)

---

# POC Sigma Foodservice

## Qué se construyó

Bot de atención a proveedores de Sigma Alimentos Foodservice con tres componentes adicionales al repo base:

| Componente | Estado | Descripción |
|---|---|---|
| **Bot RAG con FAQs** | ✅ Activo | Responde preguntas sobre pedidos, entregas, catálogo, crédito y contacto en español (es_419) usando Bedrock Knowledge Base + Claude Haiku 4.5 |
| **Autenticación de proveedores** | ✅ Activo | Lambda que identifica al proveedor por número de teléfono antes de que el bot responda |
| **Mensajes proactivos** | ✅ Activo (modo simulación) | Lambda + EventBridge que dispara mensajes de recordatorio de pedido y aviso de bloqueo. Activación automática lunes a viernes a las 8am hora México (13:00 UTC) |
| **WhatsApp** | ⏳ Pendiente Embedded Sign-Up | Stack CloudFormation listo. Requiere completar registro con Meta para obtener el número. Mientras tanto: usar Web Chat Widget |
| **DynamoDB (mock Snowflake)** | ✅ Activo | 3 tablas: proveedores, log de mensajes proactivos, sesiones. 15 proveedores de prueba cargados |

---

## Arquitectura

```
                    ┌─────────────────────────────────────────┐
                    │           Amazon Connect                  │
                    │  ┌─────────────────────────────────┐     │
 WhatsApp / Chat ──►│  │      Sigma POC Contact Flow      │     │
                    │  │                                  │     │
                    │  │  1. Auth Lambda (identifica       │     │
                    │  │     proveedor por teléfono)       │     │
                    │  │  2. Amazon Lex Bot               │     │
                    │  │     (es_419, intents Sigma)       │     │
                    │  └──────────────┬──────────────────┘     │
                    └─────────────────┼───────────────────────-┘
                                      │
                    ┌─────────────────▼───────────────────────┐
                    │         Lambda: sigma-poc-bot-handler    │
                    │  • Detecta intención (pedidos/entregas/  │
                    │    catálogo/crédito/general)             │
                    │  • Consulta Bedrock Knowledge Base        │
                    │  • Genera respuesta con Claude Haiku 4.5 │
                    └─────────────────────────────────────────┘

                    ┌─────────────────────────────────────────┐
                    │     EventBridge (Lun-Vie 8am MX)         │
                    │              │                           │
                    │              ▼                           │
                    │   Lambda: sigma-proactive-poc            │
                    │   • Escanea DynamoDB (proveedores)       │
                    │   • Clasifica: recordatorio / bloqueo    │
                    │   • Envía via Connect Outbound Campaigns │
                    └─────────────────────────────────────────┘

                    ┌─────────────────────────────────────────┐
                    │            DynamoDB                      │
                    │  • sigma-providers-poc  (15 proveedores) │
                    │  • sigma-sessions-poc   (TTL 8h)         │
                    │  • sigma-proactive-log-poc (TTL 7 días)  │
                    └─────────────────────────────────────────┘
```

---

## Recursos desplegados

> **Cuenta AWS:** `825765398662` · **Región:** `us-east-1`

| Recurso | ID / Nombre | Stack |
|---|---|---|
| Amazon Connect Instance | `b2116e6f-1693-4abf-8f91-a47920a8a38c` | `instances-skeleton-825765398662-us-east-1` |
| Contact Flow | `66bf08cf-b1eb-4a9e-ad58-da827c6a1f4d` | `sigma-poc-rag-solution` |
| Lex Bot | `UKCXRKYGFR` | `sigma-poc-rag-solution` |
| Lex Bot Alias | `SQYD2WAVEK` | `sigma-poc-rag-solution` |
| Bedrock Knowledge Base | `8S8WC4DRZY` | `sigma-poc-knowledge-base` |
| S3 – Contenido KB | `sigma-poc-kb-content-825765398662` | — |
| S3 – Artefactos | `sigma-poc-artifacts-825765398662` | — |
| Lambda – Bot handler | `sigma-poc-bot-handler` _(desde stack RAG)_ | `sigma-poc-rag-solution` |
| Lambda – Auth | `sigma-auth-handler-poc` | `sigma-poc-customer-auth` |
| Lambda – Proactivo | `sigma-proactive-poc` | `sigma-poc-proactive-messages` |
| DynamoDB – Proveedores | `sigma-providers-poc` | `sigma-poc-providers-db` |
| DynamoDB – Sesiones | `sigma-sessions-poc` | `sigma-poc-providers-db` |
| DynamoDB – Log proactivo | `sigma-proactive-log-poc` | `sigma-poc-providers-db` |

---

## Cómo probar ahora (Web Chat Widget)

### Paso 1 — Acceder al Agent Workspace de Connect

1. Ir a **AWS Console → Amazon Connect**
2. Clic en la instancia **`instances-skeleton-825765398662-us-east-1`**
3. En la página de la instancia, clic en **"Iniciar sesión en Contact Control Panel"** (o el login URL)
4. Iniciar sesión con las credenciales de Connect

### Paso 2 — Crear el widget de prueba

1. En el Agent Workspace ir a **Canales → Chat → Test chat**  
   *(o desde la consola de Connect: Canales → Chat → Configuración del chat de prueba)*
2. Seleccionar el Contact Flow: **"Sigma POC Contact Flow"**
3. Clic en **"Test"**

### Paso 3 — Conversar con el bot

El bot responde en español (es_419). Preguntas de ejemplo:

```
"¿Cuáles son los horarios de atención?"
"¿Cómo hago un pedido?"
"¿Tienen jamón en el catálogo?"
"¿Qué pasa si mi cuenta está bloqueada?"
"Quiero hablar con un agente"
```

> **Nota:** La Lambda de autenticación está conectada al Contact Flow. Al iniciar el chat, el bot identifica al proveedor por número de teléfono automáticamente. Usá cualquiera de los números de la [tabla de proveedores](#proveedores-de-prueba) para ver la autenticación en acción.

---

## Proveedores de prueba

Los siguientes 15 proveedores están cargados en DynamoDB. Cuando la autenticación esté conectada al Contact Flow, se pueden usar estos teléfonos para probar los distintos escenarios.

| ID | Nombre | Teléfono | Estado | Escenario |
|---|---|---|---|---|
| PROV-GDL-001 | Restaurante El Toro Bravo | +523312345678 | ACTIVO | Sin proactivo (pedido reciente) |
| PROV-GDL-002 | Hotel Real de Guadalajara | +523387654321 | ACTIVO | Recordatorio (17 días sin pedir) |
| PROV-GDL-003 | Distribuidora Alimentos del Bajío | +523311122233 | BLOQUEADO | Aviso de bloqueo |
| PROV-GDL-004 | Cafetería Los Pinos | +523344455566 | ACTIVO | Recordatorio (22 días sin pedir) |
| PROV-GDL-005 | Supermercado La Colonia Zapopan | +523366677788 | ACTIVO | Sin proactivo (pedido reciente) |
| PROV-GDL-006 | Taquería El Compadre | +523399900011 | SUSPENDIDO | Aviso de bloqueo |
| PROV-GDL-007 | Catering Eventos Premier | +523322233344 | ACTIVO | Sin WhatsApp → excluido de proactivos |
| PROV-GDL-008 | Mini Super Don Nacho | +523355566677 | ACTIVO | Sin opt-in → excluido de proactivos |
| PROV-GDL-009 | Restaurante Hacienda del Sol | +523388899900 | ACTIVO | Recordatorio (15 días sin pedir) |
| PROV-GDL-010 | Club Deportivo Chivas Canteen | +523311100022 | BLOQUEADO | Aviso de bloqueo |
| PROV-GDL-011 | Panadería y Pastelería Dulce Hogar | +523344400033 | ACTIVO | Sin proactivo (pedido muy reciente) |
| PROV-GDL-012 | Hotel Boutique Casa Tapatía | +523377788899 | ACTIVO | Recordatorio (28 días sin pedir) |
| PROV-GDL-013 | Comedor Industrial Cemex GDL | +523366655544 | ACTIVO | Sin proactivo (pedido hace 5 días) |
| PROV-GDL-014 | Cremería y Lácteos La Joya | +523399900077 | ACTIVO | Recordatorio (13 días sin pedir) |
| PROV-GDL-015 | Lonchería Los Compadres | +523311101133 | BLOQUEADO | Aviso de bloqueo |

---

## Escenarios de prueba

### Escenario 1 — Bot RAG (disponible HOY)

Probar desde el Web Chat Widget sin autenticación:

| Pregunta | Resultado esperado |
|---|---|
| "¿Cuáles son sus horarios?" | Responde con horarios de Sigma (FAQs) |
| "¿Cómo hago un pedido?" | Responde con el proceso de pedidos |
| "¿Tienen queso Oaxaca?" | Responde con info del catálogo |
| "¿Qué pasa con mi crédito?" | Responde sobre proceso de crédito |
| "Necesito hablar con alguien" | Transfiere a agente (SpeakToAgent intent) |
| Pregunta fuera de tema | El bot responde que no tiene info y ofrece alternativas |

### Escenario 2 — Autenticación (disponible al conectar Lambda al Contact Flow)

Cuando el Contact Flow invoque la Lambda auth antes de Lex:

| Acción | Resultado esperado |
|---|---|
| Conectarse con teléfono de PROV-GDL-001 | Atributos de contacto: `authenticated=true`, `providerName=Restaurante El Toro Bravo`, `creditStatus=ACTIVO` |
| Conectarse con teléfono de PROV-GDL-003 | `authenticated=true`, `creditStatus=BLOQUEADO`, `isBlocked=true` |
| Conectarse con número no registrado | `authenticated=false`, modo anónimo |

### Escenario 3 — Mensajes proactivos (simulación)

La Lambda de proactivos se ejecuta lunes a viernes a las 8am hora México. Para forzar una ejecución de prueba:

```bash
aws lambda invoke \
    --function-name sigma-proactive-poc \
    --payload '{}' \
    --region us-east-1 \
    --profile sigma-poc \
    /tmp/proactive-output.json && cat /tmp/proactive-output.json
```

**Resultado esperado:** log con proveedores clasificados como `recordatorio` o `bloqueo`, y mensajes en modo simulación (campaignId = `PENDING`).

Para ver el log en CloudWatch:
- **CloudWatch → Log groups → `/aws/lambda/sigma-proactive-poc`**

---

## Intents del bot

El bot tiene los siguientes intents configurados en Lex (es_419):

| Intent | Descripción |
|---|---|
| `ConsultaGeneral` | Preguntas generales sobre Sigma |
| `ConsultaPedidos` | Consultas sobre pedidos |
| `ConsultaEntregas` | Consultas sobre entregas y logística |
| `ConsultaCatalogo` | Consultas sobre productos del catálogo |
| `ConsultaCreditoPagos` | Consultas sobre crédito, saldo y pagos |
| `SpeakToAgent` | El proveedor pide hablar con un agente |
| `Help` | Pide ayuda sobre qué puede hacer el bot |
| `Goodbye` | Despedida |
| `FallbackIntent` | Todo lo que no matchea otro intent (también va a RAG) |
| `SelectLLM` _(testing)_ | Cambiar el modelo LLM en runtime |
| `SelectKnowledgeBase` _(testing)_ | Cambiar la Knowledge Base en runtime |
| `ToggleLLMContext` _(testing)_ | Activar/desactivar contexto conversacional |
| `ToggleLLMGuardrails` _(testing)_ | Activar/desactivar guardrails del prompt |

---

## Pasos manuales pendientes

### ✅ Paso 1 — Lambda de autenticación conectada al Contact Flow

La Lambda `sigma-auth-handler-poc` está desplegada, registrada en Connect, **y conectada al Contact Flow via API**. El flujo actual es:

```
Welcome → InvokeLambda(sigma-auth-handler-poc) → SetAuthAttrs → SetSessionAttrs → LexBot
```

**Atributos que devuelve la Lambda (ya disponibles como Contact Attributes):**

| Atributo | Descripción |
|---|---|
| `authenticated` | `"true"` o `"false"` |
| `providerId` | ID del proveedor (ej: `PROV-GDL-001`) |
| `providerName` | Nombre del negocio |
| `sucursal` | Sucursal Sigma asignada |
| `creditStatus` | `ACTIVO`, `BLOQUEADO`, o `SUSPENDIDO` |
| `isBlocked` | `"true"` si está bloqueado/suspendido |
| `creditLimit` | Límite de crédito (string) |
| `balance` | Saldo actual (string) |
| `lastOrderDate` | Fecha del último pedido |
| `providerSummary` | Resumen en texto para el bot |

### 🟡 Paso 2 — Crear campañas de WhatsApp en Connect (cuando esté WhatsApp)

Cuando se complete el Embedded Sign-Up con Meta:

1. **Connect Console → Outbound Campaigns → Crear campaña "Recordatorio de pedido"**
   - Tipo: WhatsApp
   - Contact Flow: el que corresponda para proactivos
2. **Crear campaña "Aviso de bloqueo"** (similar)
3. Re-ejecutar deploy con los IDs:
   ```bash
   ./deploy-sigma.sh --stack sigma-proact \
     --bucket sigma-poc-artifacts-825765398662 \
     --connect-arn arn:aws:connect:us-east-1:825765398662:instance/b2116e6f-1693-4abf-8f91-a47920a8a38c \
     --recordatorio-id <id-campana> \
     --bloqueo-id <id-campana>
   ```

### 🟡 Paso 3 — WhatsApp Embedded Sign-Up con Meta

1. **AWS Console → Connect → Canales → WhatsApp**
2. Completar el proceso de Embedded Sign-Up
3. Obtener `WhatsAppPhoneNumberId` y `WhatsAppWABAId`
4. Re-ejecutar deploy:
   ```bash
   ./deploy-sigma.sh --stack sigma-wa \
     --bucket sigma-poc-artifacts-825765398662 \
     --connect-arn arn:aws:connect:us-east-1:825765398662:instance/b2116e6f-1693-4abf-8f91-a47920a8a38c \
     --wa-phone-id <phone-number-id> \
     --wa-waba-id <waba-id>
   ```

### 🟢 Paso 4 — Sincronizar Knowledge Base (al agregar contenido nuevo)

Si se agrega contenido a `content/sigma/`:
```bash
# Subir contenido
aws s3 sync content/sigma/ s3://sigma-poc-kb-content-825765398662/sigma/ \
    --region us-east-1 --profile sigma-poc

# Re-sincronizar la KB en la consola:
# AWS Console → Bedrock → Knowledge Bases → sigma-poc-knowledge-base → Sync
```

---

## Re-deploy y mantenimiento

### Script de deploy completo

```bash
./deploy-sigma.sh \
  --bucket sigma-poc-artifacts-825765398662 \
  --connect-arn arn:aws:connect:us-east-1:825765398662:instance/b2116e6f-1693-4abf-8f91-a47920a8a38c \
  --content-bucket sigma-poc-kb-content-825765398662 \
  --region us-east-1
```

### Re-deploy de un stack específico

```bash
# Solo la Knowledge Base
./deploy-sigma.sh --stack base-kb --bucket sigma-poc-artifacts-825765398662 ...

# Solo el bot (Lex + Lambda + Contact Flow)
./deploy-sigma.sh --stack base-rag --bucket sigma-poc-artifacts-825765398662 ...

# Solo DynamoDB
./deploy-sigma.sh --stack sigma-db --bucket sigma-poc-artifacts-825765398662 ...

# Solo Lambda de auth
./deploy-sigma.sh --stack sigma-auth --bucket sigma-poc-artifacts-825765398662 ...

# Solo Lambda de mensajes proactivos
./deploy-sigma.sh --stack sigma-proact --bucket sigma-poc-artifacts-825765398662 ...

# Re-seed de proveedores
./deploy-sigma.sh --stack seed --bucket sigma-poc-artifacts-825765398662 ...

# Solo contenido (FAQs + catálogo)
./deploy-sigma.sh --stack content --bucket sigma-poc-artifacts-825765398662 ...
```

### Forzar ejecución manual de mensajes proactivos

```bash
aws lambda invoke \
    --function-name sigma-proactive-poc \
    --payload '{}' \
    --region us-east-1 \
    --profile sigma-poc \
    /tmp/proactive-output.json
```

---

## Estructura de archivos Sigma

```
contact-center-genai-agent/
│
├── content/sigma/                      # Contenido indexado en la Knowledge Base
│   ├── faqs/
│   │   ├── pedidos.md                  # FAQs sobre pedidos
│   │   ├── entregas.md                 # FAQs sobre entregas
│   │   ├── catalogo.md                 # FAQs sobre catálogo
│   │   ├── credito-pagos.md            # FAQs sobre crédito y pagos
│   │   └── contacto-horarios.md        # FAQs sobre contacto y horarios
│   └── catalogo/
│       ├── jamon-cocido-premium.md     # Ficha técnica Jamón Cocido Premium
│       ├── queso-oaxaca.md             # Ficha técnica Queso Oaxaca
│       └── salchicha-viena.md          # Ficha técnica Salchicha Viena
│
├── infrastructure/sigma/               # CloudFormation templates adicionales
│   ├── sigma-providers-db.yaml         # DynamoDB: proveedores, sesiones, log proactivo
│   ├── sigma-customer-auth.yaml        # Lambda de autenticación de proveedores
│   ├── sigma-proactive-messages.yaml   # Lambda + EventBridge mensajes proactivos
│   └── sigma-whatsapp.yaml             # Integración WhatsApp (pendiente Meta)
│
├── src/sigma/                          # Código fuente Lambda de Sigma
│   ├── auth/
│   │   └── handler.py                  # Lambda: autenticación por teléfono en DynamoDB
│   ├── proactive-messages/
│   │   └── handler.py                  # Lambda: mensajes proactivos vía Connect Campaigns
│   └── seed/
│       ├── seed_providers.py           # Script de seed de proveedores en DynamoDB
│       └── providers_data.json         # 15 proveedores ficticios de prueba
│
├── src/lex/hotel-bot-handler/          # Lambda handler del bot (modificado para Sigma)
│   ├── handler.py                      # Intents de Sigma (ConsultaPedidos, etc.)
│   ├── TopicIntentHandler.py           # Filtros por categoría (faqs/, catalogo/)
│   └── bedrock_utils/
│       ├── models/anthropic.py         # + Claude Haiku 4.5 (inference profile us.)
│       ├── conversational_agents/
│       │   └── anthropic.py            # Prompts en es_419, personalidad Sigma
│       └── hotel_agents/               # (renombrado semánticamente, sin cambios)
│
├── parameters/sigma-poc.json           # Todos los parámetros y IDs del deploy
├── deploy-sigma.sh                     # Script maestro de deploy
└── prompts/sigma/
    └── system-prompt-es419.txt         # Prompt de sistema del bot (personalidad Sigma)
```

---

## Cambios al proyecto base

| Archivo modificado | Cambio |
|---|---|
| `infrastructure/bedrock-KB.yaml` | Fix `InclusionPrefixes` como lista (compatibilidad CloudFormation Early Validation) |
| `infrastructure/contact-center-RAG-solution.yaml` | Parámetros `pLexLocaleId` y `pLexVoiceId`; elimina `AudioRecognitionStrategy` (no soportado en `es_419`) |
| `src/lex/hotel-bot-handler/handler.py` | Reemplaza intents de hotel por intents de Sigma en español |
| `src/lex/hotel-bot-handler/TopicIntentHandler.py` | Filtros por categoría S3 en lugar de marcas de hotel |
| `src/lex/hotel-bot-handler/bedrock_utils/models/anthropic.py` | Agrega Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) |
| `src/lex/hotel-bot-handler/bedrock_utils/conversational_agents/anthropic.py` | Prompts en es_419, personalidad y vocabulario Sigma |
| `src/lex/hotel-bot-handler/bedrock_helpers.py` | Default → Claude Haiku 4.5 |
| `src/hallucinations/.../models/anthropic.py` | Agrega Claude Haiku 4.5 |

---

# Proyecto base AWS (referencia)

**_An automated question answering solution for contact centers, optimized for both text and voice._**

El README completo del proyecto base está disponible en el [repositorio original de AWS](https://github.com/aws-samples/contact-center-genai-agent).

## Arquitectura base

<p align="center">
    <img src=images/architecture.png alt="architecture" width="100%">
</p>

## Deploy base (hotel-bot de ejemplo)

Para el deploy del proyecto base (hotel bot en inglés), consultar la [documentación original](https://github.com/aws-samples/contact-center-genai-agent#deploy-and-test-the-solution).

Los stacks base son:
1. `infrastructure/bedrock-KB.yaml` — Knowledge Base + OpenSearch Serverless
2. `infrastructure/detect-hallucinations.yaml` — Detección de alucinaciones _(opcional)_
3. `infrastructure/contact-center-RAG-solution.yaml` — Lex Bot + Lambda + Contact Flow
4. `infrastructure/lex-data-pipeline.yaml` — Analytics con QuickSight _(opcional)_

## Modelos de Bedrock requeridos

Habilitar en **AWS Console → Bedrock → Acceso al modelo**:
- Amazon Titan Embeddings G1 – Text v2
- **Anthropic Claude Haiku 4.5** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) ← modelo activo en la cuenta

> **Nota:** Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`) figura como LEGACY en esta cuenta. Usar Haiku 4.5 con inference profile.

## Estructura del proyecto base

- [content](content) — Documentos de ejemplo (hotel chains)
- [infrastructure](infrastructure) — CloudFormation templates
- [src](src) — Código fuente Python
- [notebooks](notebooks) — Jupyter notebooks para testing automatizado
- [test/test-runs](test/test-runs) — Resultados de tests

---

## Contributors

**Sigma Foodservice POC**
- Silvana Garcia, Nubity

**Proyecto base (aws-samples)**
- Brian Yost, Principal Deep Learning Architect, AWS Generative AI Innovation Center
- Alvaro Sanchez Martin, Senior Solutions Architect, ISV
