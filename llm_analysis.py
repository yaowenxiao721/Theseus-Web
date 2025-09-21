import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime

import openai
import httpx
import transformers
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

class bcolors:
    if sys.stdout.isatty():
        HEADER = '\033[95m'
        OKBLUE = '\033[94m'
        OKCYAN = '\033[96m'
        OKGREEN = '\033[92m'
        WARNING = '\033[93m'
        FAIL = '\033[91m'
        ENDC = '\033[0m'
        BOLD = '\033[1m'
        UNDERLINE = '\033[4m'
    else:
        HEADER = ''
        OKBLUE = ''
        OKCYAN = ''
        OKGREEN = ''
        WARNING = ''
        FAIL = ''
        ENDC = ''
        BOLD = ''
        UNDERLINE = ''

def setup_logger(name, log_file):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s\t%(name)s\t%(levelname)s\t[%(filename)s:%(lineno)d]\t%(message)s',
                                      datefmt='%Y-%m-%d:%H:%M:%S')
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logger.propagate = False
    return logger

long_timeout_async_client = httpx.AsyncClient(timeout=180)
async_client = openai.AsyncOpenAI(api_key=os.getenv("API_KEY"), base_url=os.getenv("BASE_URL"), timeout=180, http_client=long_timeout_async_client)
max_crawl_time = float(os.getenv("MAX_CRAWL_TIME", 8 * 60 * 60))
start_time = time.time()

tokenizer = transformers.AutoTokenizer.from_pretrained(os.getcwd(), trust_remote_code=True)

timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
app_name = os.getenv("APP_NAME", "")
log_file_name = os.path.join(os.getcwd(), 'logs', app_name + '-llm-' + str(timestamp) + '.log')
llm_logger = setup_logger('llm', log_file_name)

failed_analysis_prompt = {}

def is_similar(prompt, cache, threshold=0.95):
    if not cache:
        return None

    prompts = list(cache.keys())
    vectorizer = TfidfVectorizer().fit_transform([prompt] + prompts)
    similarity_matrix = cosine_similarity(vectorizer[0:1], vectorizer[1:])
    max_sim_idx = similarity_matrix.argmax()
    max_sim_value = similarity_matrix[0, max_sim_idx]

    if max_sim_value >= threshold:
        return prompts[max_sim_idx]
    return None

async def identify_resource_operation_before_request(purpose, prompt):
    system_prompt_template = """You are a penetration testing expert. Below is a description of a web application that you
need to analyze. The purpose of this application are {purpose}.

User will provide you with various request actions from this web application. Your task is to analyze each request action and
determine what operation will be performed on which resource. In addition, please categorize the operation into
one of the following CRUD types: create, read, update, delete, unknown or block.

You need to determine whether the request action involves a potentially block operation. A request action is considered block if it:
0. Delete any user account or any user data.
1. Updates or modifies authentication credentials (e.g., password changes, email change).
2. Alters user roles, permissions, or access control settings.
3. Logs out the current user or terminates other active sessions.
4. Changes ownership or participation status of a resource (e.g., transferring account ownership, revoking user access).
5. Performs other actions that may disrupt the application's normal behavior or user access.
6. Operations related to plugins, extensions, or modules, especially those that can affect the core functionality, access control, permission settings, or user experience of the system.

### Resource Name Formatting
Ensure that resource names are formatted as meaningful words or phrases with spaces separating words. Avoid special characters or concatenated terms. Additionally, consider whether the request applies to **a specific resource** (singular) or **a category of resources** (plural), and format accordingly.

Please answer in the following JSON format:
{{"operation": "action", "resource": "resource type", "CRUD_type": "CRUD category"}}.

For example, if the request action is about deleting an order, the answer should be:
{{"operation": "delete", "resource": "order", "CRUD_type": "delete"}}.

If the request action is about updating a user's password, the answer should be:
{{"operation": "update", "resource": "password", "CRUD_type": "block"}}.

Now, please analyze the provided request action and determine the operation, resource, CRUD type.

If you are unable to determine the operation or resource, please return {{}} or {{"operation": "unknown", "resource": "unknown", "CRUD_type": "unknown"}}.
    """

    system_prompt = system_prompt_template.format(purpose=purpose)

    conversation = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': prompt}]

    start = time.time()
    error = False
    try:
        try:
            response = await async_client.chat.completions.create(
                model=os.getenv("MODEL_NAME"),
                messages=conversation,
                response_format={
                    'type': 'json_object'
                }
            )
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Please wait for 1 minute before trying again" in error_msg:
                llm_logger.error("Rate limit exceeded: " + error_msg)
                print(bcolors.OKBLUE + "Rate limit exceeded: " + error_msg + bcolors.ENDC)
                await asyncio.sleep(60)
                response = None
                error = True
            else:
                llm_logger.error("LLM API error: " + error_msg)
                print(bcolors.OKBLUE + "LLM API error: " + error_msg + bcolors.ENDC)
                response = None
                error = True

        if response is not None:
            answer = response.choices[0].message.content
        else:
            answer = "{}"

        try:
            answer = json.loads(answer)
            if answer == {}:
                llm_logger.warning("Failed to identify resource operation: " + str(answer))
                print(bcolors.OKBLUE+"Failed to identify resource operation: " + str(answer)+bcolors.ENDC)
            elif not 'operation' in answer or not 'resource' in answer:
                llm_logger.error("Failed to generate resource operation: " + str(answer))
                print(bcolors.OKBLUE+"Failed to generate resource operation: " + str(answer)+bcolors.ENDC)
                answer = {}
                error = True
            else:
                if 'operation' in answer and 'resource' in answer and 'CRUD_type' in answer:
                    if answer['operation'] == "unknown" or answer['resource'] == "unknown":
                        llm_logger.warning("Unknown resource operation: " + str(answer))
                        print(bcolors.OKBLUE+"Unknown resource operation: " + str(answer)+bcolors.ENDC)
                else:
                    print(bcolors.OKBLUE+"Failed to parse response: " + str(answer)+bcolors.ENDC)
                    answer = {}
                    error = True
        except Exception as e:
            llm_logger.error("Parsing error: " + str(e))
            llm_logger.error("Failed to parse response: " + str(answer))
            print(bcolors.OKBLUE+"Failed to parse response: " + str(answer)+bcolors.ENDC)
            answer = {}
            error = True

    except Exception as e:
        llm_logger.error(f"Failed to generate response: {str(e)}")
        print(bcolors.OKBLUE+"Failed to generate response: "+str(e)+bcolors.ENDC)
        answer = {}
        error = True

    if answer == {}:
        answer = {"operation": "unknown", "resource": "unknown", "CRUD_type": "unknown"}
    return answer, error

async def analyze_request(request_queue, analysis_queue, still_crawling_signal, cache, cache_lock, hash_cache):
    model_tpm = int(os.getenv("MODEL_TPM", 1000000))
    model_tpm = 0.6 * model_tpm
    model_qpm = int(os.getenv("MODEL_QPM", 1200))
    model_qpm = 0.6 * model_qpm

    while still_crawling_signal.is_set():
        if not request_queue.empty():
            tasks = []
            total_batch_size = 0
            total_batch_token_length = 0

            for failed_prompt in failed_analysis_prompt:
                if failed_analysis_prompt[failed_prompt]["retry_times"] == 1:
                    request_wrapper = {
                        'prompt': failed_prompt,
                        'req_index': failed_analysis_prompt[failed_prompt]["req_index"],
                        'edge_index': failed_analysis_prompt[failed_prompt]["edge_index"],
                    }
                    token_length = len(tokenizer.encode(failed_prompt))
                    total_batch_token_length += token_length
                    total_batch_size += 1
                    llm_logger.info("Retry analysis: " + str(request_wrapper['edge_index']))
                    print(bcolors.OKBLUE + "Retry analysis: " + str(request_wrapper['edge_index']) + bcolors.ENDC)
                    tasks.append(asyncio.create_task(
                        llm_wrapper(request_wrapper, time.time(), analysis_queue, cache, cache_lock,
                                    hash_cache)))

                    if total_batch_token_length > model_tpm or total_batch_size > model_qpm:
                        break

            while not request_queue.empty():
                if request_queue.empty():
                    break
                request_wrapper = request_queue.get()
                prompt = request_wrapper['prompt']
                token_length = len(tokenizer.encode(prompt))
                total_batch_token_length += token_length
                total_batch_size += 1
                tasks.append(asyncio.create_task(llm_wrapper(request_wrapper, time.time(), analysis_queue, cache, cache_lock, hash_cache)))

                if total_batch_token_length > model_tpm or total_batch_size > model_qpm:
                    break

            if tasks:
                start = time.time()
                formatted_start = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
                print(bcolors.OKBLUE+"Start analyzing batch, start time: "+str(formatted_start)+bcolors.ENDC)
                await asyncio.gather(*tasks)
                MODEL_WAIT_TIME = int(os.getenv("MODEL_WAIT_TIME", 60))
                await asyncio.sleep(MODEL_WAIT_TIME)
            else:
                await asyncio.sleep(1)

        else:
            await asyncio.sleep(5)

async def llm_wrapper(request_wrapper, start, analysis_queue, cache, cache_lock, hash_cache):
    purpose = os.getenv("PURPOSE", "")
    req_index = request_wrapper['req_index']
    prompt = request_wrapper['prompt']
    edge_index = request_wrapper['edge_index']
    print(bcolors.OKBLUE+"Start llm analysis"+bcolors.ENDC)

    analysis = {}
    find_similar = False
    find_same = False
    error = False
    key_hash = hashlib.sha256(prompt.encode()).hexdigest()
    if key_hash in hash_cache:
        find_same = True
        print(bcolors.OKBLUE+"Found same prompt"+bcolors.ENDC)
        llm_logger.info("Found same prompt: \n" + str(prompt))
        analysis = hash_cache[key_hash]
    if not find_same and 'is_event' in request_wrapper:
        async with cache_lock:
            similar_prompt = is_similar(prompt, cache)
            if similar_prompt:
                print(bcolors.OKBLUE+"Similar prompt found"+bcolors.ENDC)
                llm_logger.info("Similar prompt found: \n" + str(prompt) + "\nSimilar prompt: \n" + str(similar_prompt))
                find_similar = True
                analysis = cache[similar_prompt]

    if not find_same and not find_similar:
        analysis, error = await identify_resource_operation_before_request(purpose, prompt)
        async with cache_lock:
            if analysis and analysis != {}:
                cache[prompt] = analysis
                hash_cache[key_hash] = analysis

    if not error:
        analysis_wrapper = {
            "req_index": req_index,
            "edge_index": edge_index,
            "analysis": analysis
        }

        analysis_queue.put(analysis_wrapper)
        if prompt in failed_analysis_prompt:
            del failed_analysis_prompt[prompt]
    else:
        if prompt not in failed_analysis_prompt:
            failed_analysis_prompt[prompt] = {
                "req_index": req_index,
                "edge_index": edge_index,
                "retry_times": 1
            }
        else:
            failed_analysis_prompt[prompt]["retry_times"] += 1

    print(bcolors.OKBLUE+"Total time: "+ str(time.time() - start)+bcolors.ENDC)
    print(bcolors.OKBLUE+"Analysis for index: "+str(edge_index)+" is "+str(analysis)+bcolors.ENDC)

def run_llm_analysis(request_queue, analysis_queue, condition_signal, still_crawling_signal):
    cache = {}
    cache_lock = asyncio.Lock()
    hash_cache = {}
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(
        analyze_request(request_queue, analysis_queue, still_crawling_signal, cache, cache_lock, hash_cache))
    loop.close()