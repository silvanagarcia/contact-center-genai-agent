# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import logging
import json
import os
import dialog_helpers
import slot_configuration
import bedrock_helpers

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

MAX_CONVERSATION_TURNS = int(os.environ.get('CONVERSATION_TURNS', '4'))
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
ANY_CATEGORY = 'Any'

def lambda_handler(event, context):
    requestAttributes = event.get("requestAttributes", {})
    sessionState = event.get('sessionState', {})
    sessionAttributes = sessionState.get("sessionAttributes", {})
    activeContexts = sessionState.get("activeContexts", [])
    intent = sessionState.get("intent", {})
    intent_name = intent['name']
    slot_values = intent.get('slots', {})

    retrieved_context = None

    logger.info('<<{}>> - Lex event info {} '.format(intent_name, json.dumps(event)))
    
    if not (agent := bedrock_helpers.select_conversational_agent(sessionAttributes.get('ragLLM'))):
        # set prompt_id and prompt for analytics
        sessionAttributes['prompt_id'] = intent_name + '-LLM-Config-Error'
        response_template = 'Configuration error, LLM = "{llm}"'
        sessionAttributes['prompt'] = response_template
        response_string = response_template.format(llm = sessionAttributes.get('ragLLM', 'None'))
        response_message = dialog_helpers.format_message_array(response_string, 'PlainText')
        logger.error(response_message)
        
    else:
        input_transcript = event.get('inputTranscript')
        
        # determine the KB folder filter based on the intent (Sigma content categories)
        # intent_name maps to a subfolder in the S3 Knowledge Base bucket
        category = INTENT_CATEGORY_MAP.get(intent_name, '')

        # retrieve conversation turns, if any
        conversation = ''
        user_questions_only = ''
        last_response = ''
        conversation_data = sessionAttributes.get('conversation')
        turns = []
        if conversation_data:
            try:
                turns = json.loads(conversation_data)
                max_turns = min(len(turns), MAX_CONVERSATION_TURNS)
                for i in range(0, max_turns):
                    conversation += f"Q: {turns[i]['Q']}\nA: {turns[i]['A']}\n"
                    user_questions_only += f"{turns[i]['Q']}\n"
                    last_response = turns[i]['A']
            except:
                pass
        
        if len(conversation) > 0:
            rolling_conversation = "CONVERSATION HISTORY:\n" + conversation + "\nQUESTION: " + input_transcript
        else:
            rolling_conversation = input_transcript
        
        # special case: if only a single brand has been mentioned in the last response,
        # and the user did not specifically ask about other brands, set the brand filter 
        # to the single brand mentioned for conversational context
        if (single_brand := single_brand_mentioned(last_response)):
            if not slot_values.get('brand'):
                brand = single_brand
                sessionAttributes['brand'] = brand

        logger.debug(f'TopicIntentHandler: sessionAttributes = {json.dumps(sessionAttributes, indent=4)}')
        logger.debug(f'TopicIntentHandler: input_transcript = {input_transcript}')

        # get a Bedrock Knowledge Base instance
        knowledge_base = sessionAttributes.get('knowledgeBase', 'Default')
        bedrock_kb = bedrock_helpers.select_knowledge_base(knowledge_base)

        # set the query filter based on S3 folder structure for Sigma content
        if bedrock_kb.s3_bucket is not None and len(bedrock_kb.s3_bucket) > 0:
            folder_prefix = get_category_filter(category)
            query_filter = {
                'startsWith': {
                    'key': 'x-amz-bedrock-kb-source-uri',
                    'value': 's3://' + bedrock_kb.s3_bucket + folder_prefix
                }
            }
            bedrock_kb.metadata_filter = query_filter
            logger.debug(f'KB s3_bucket = {bedrock_kb.s3_bucket}')

        logger.debug(f'KB = {bedrock_kb.kb_instance_name}')
        logger.debug(f'KB threshold = {bedrock_kb.threshold}')
        logger.debug(f'KB max_docs = {bedrock_kb.max_docs}')
        logger.debug(f'KB search_type = {bedrock_kb.search_type}')
        logger.debug(f'KB metadata_filter = {json.dumps(bedrock_kb.metadata_filter, indent=4)}')
        
        # retrieve context to pass to the LLM based on selected brand, if any
        # note: max query length is 1000 characters for Bedrock KB
        logger.info(f'BEDROCK KB Query = {rolling_conversation[-500:]}')
        response = bedrock_kb.retrieve_context(query=rolling_conversation[-500:])

        retrieval_time = response.get('invocation_time')
        retrieved_context = response.get('context', 'No information is available on this topic.')
        logger.debug(f'retrieved_context = {retrieved_context}')
        
        logger.info(f'agent model ID = {agent.model_instance.model_id}')

        # generate the response
        agent.context = sessionAttributes.get('context_switch', '1') == '1'
        agent.guardrails = sessionAttributes.get('guardrails_switch', '1') == '1'
        
        agent_response = agent.generate_response(retrieved_context, rolling_conversation)
        
        prompt = agent_response.get('prompt')
        rag_response = agent_response.get('response')
        logger.info('LLM RESPONSE = {}'.format(json.dumps(rag_response, indent=4)))
        logger.info('LLM PROMPT = {}'.format(prompt))
        
        # add latest turn to the conversation
        turns.append({'Q': input_transcript, 'A': rag_response})
        if len(turns) > MAX_CONVERSATION_TURNS:
            turns.pop(0)
        sessionAttributes['conversation'] = json.dumps(turns)
        logger.debug(f'END CONVERSATION = {json.dumps(turns, indent=4)}')

        # capture session attributes for analytics
        sessionAttributes['knowledge_base'] = bedrock_kb.kb_id
        sessionAttributes['retrieval_latency'] = retrieval_time
        sessionAttributes['rag_llm'] = agent.model_instance.model_id
        sessionAttributes['rag_request_id'] = agent_response.get('request_id')
        sessionAttributes['rag_input_tokens'] = agent_response.get('input_tokens')
        sessionAttributes['rag_output_tokens'] = agent_response.get('output_tokens')
        sessionAttributes['rag_latency'] = agent_response.get('invocation_time')
        sessionAttributes['total_latency'] = agent_response.get('invocation_time') + retrieval_time
        sessionAttributes['prompt_id'] = intent_name + '-LLM-Response'
        sessionAttributes['prompt'] = '(LLM response)'

        # prepare response for Lex
        response_string = rag_response
        logger.info('response_string = {}'.format(response_string))
        if event.get('inputMode', '') == 'Speech':
            for word in SPEECH_CONVERSIONS:
                response_string = response_string.replace(word, SPEECH_CONVERSIONS[word])
            response_string = '<speak>' + response_string + '</speak>'
    
        response_message = dialog_helpers.format_message_array(response_string, 'PlainText')
        action = dialog_helpers.close
        
        # queue the response for async hallucination detection evaluation
        if SQS_QUEUE_URL is not None and len(SQS_QUEUE_URL) > 0:
            bedrock_helpers.queue_hallucination_scan(event, input_transcript, rag_response, retrieved_context)

    intent['state'] = 'Fulfilled'
    response = dialog_helpers.close(intent, activeContexts, sessionAttributes, response_message, requestAttributes)
    logger.info('<<{}>>: response = {}'.format(intent_name, json.dumps(response)))

    # make this available to the handler() function for hallucination detection
    if retrieved_context:
        response['_retrieved_context'] = retrieved_context
    
    return response


# Sigma: mapa de intent → subcarpeta en S3 para filtrar la Knowledge Base
# Permite que cada intent consulte solo el contenido relevante
INTENT_CATEGORY_MAP = {
    'ConsultaGeneral':      'faqs',        # todas las FAQs
    'ConsultaPedidos':      'faqs/pedidos',
    'ConsultaEntregas':     'faqs/entregas',
    'ConsultaCatalogo':     'catalogo',
    'ConsultaCreditoPagos': 'faqs/credito-pagos',
    # FallbackIntent no tiene filtro: busca en todo el contenido
}

def get_category_filter(category):
    """Devuelve el prefijo de carpeta S3 para filtrar la KB según el intent."""
    if not category:
        return ''
    return '/' + category

def single_brand_mentioned(conversation):
    """Mantenido por compatibilidad — no aplica en Sigma (sin filtro por marca)."""
    return None

# Conversiones para texto (WhatsApp es canal de texto, no voz)
SPEECH_CONVERSIONS = {}  # No aplica para WhatsApp

