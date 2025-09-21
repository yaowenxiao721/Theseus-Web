import hashlib
import json
import os
import logging

import httpx
import time
from openai import OpenAI, AsyncOpenAI

class LLMManager:
    def __init__(self, api_key, base_url, model_name):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.long_timeout_client = httpx.Client(timeout=180)
        self.long_timeout_async_client = httpx.AsyncClient(timeout=180)
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180, http_client=self.long_timeout_client)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.after_resource_operation_cache = {}

        # 添加以下初始化行
        self.total_url_components_time = 0.0
        self.total_url_components_count = 0
        self.url_components_time = 0.0
        self.url_components_count = 0
        self.total_resource_dependency_time = 0.0
        self.total_resource_dependency_count = 0
        self.resource_dependency_time = 0.0
        self.resource_dependency_count = 0

    def identify_semantically_important_parameter(self, prompt):
        system_prompt = """We are analyzing whether a parameter in a web URL is semantically important or not.

Definition:
- A parameter is semantically important if changing its value alters the system behavior, triggers different logic, or reaches new parts of the application code.
- A parameter is semantically unimportant if changing its value does not lead to a different feature page, new retrieval result, or any additional code coverage during execution.

In making this decision, consider both:
- the semantics of the parameter name (e.g., action, page, sort)
- the semantics of the parameter value, especially when it is an identifier, UUID, or numeric ID (e.g., id=1, token=abc123, uuid=af3…). Such values often indicate references to data objects and typically do not change the operation being performed.

You will be given a URL, a parameter name and some examples of the same parameter with different values. Your task is to determine whether the parameter is semantically important or not.

Answer using this JSON format only:
{"semantically important": true} or {"semantically important": false}

Examples:

URL: http://example.com/item?id=1
Parameter Name: id
Parameter Values: 1, 2, 3
Answer: {"semantically important": false}  # Only changes object accessed, not action

URL: http://example.com/user?action=edit
Parameter Name: action
Parameter Values: edit, delete, view
Answer: {"semantically important": true}  # Changing action changes request behavior

URL: http://site.com/view?user_token=abc123
Parameter Name: user_token
Parameter Values: abc123, def456, ghi789
Answer: {"semantically important": false}  # Token used for identity, doesn't affect meaning
"""
        conversation = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': prompt}]
        start_time = time.time()
        try:
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=conversation,
                    response_format={
                        'type': 'json_object'
                    }
                )
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "Please wait for 1 minute before trying again" in error_msg:
                    logging.error("Rate limit exceeded: " + error_msg)
                    time.sleep(60)
                    response = None
                else:
                    logging.error("LLM API error: " + error_msg)
                    response = None

            if response is not None:
                answer = response.choices[0].message.content
            else:
                answer = "{}"
            try:
                answer = json.loads(answer)
                if answer == {}:
                    logging.warning("Failed to identify semantically important parameter: " + str(answer))
                elif not 'semantically important' in answer:
                    logging.error("Failed to identify semantically important parameter: " + str(answer))
                    answer = {}
            except:
                logging.error("Failed to parse response: " + str(answer))
                answer = {}
        except Exception as e:
            logging.error(f"Failed to generate response: {str(e)}")
            answer = {}
        self.total_url_components_time += time.time() - start_time
        self.total_url_components_count += 1
        self.url_components_time += time.time() - start_time
        self.url_components_count += 1
        return answer

    def identify_resource_dependency_relationship(self, prompt):
        system_prompt = """You are a penetration testing expert. Given two resource in a web application, determine whether there is a parent-child relationship between them. 
A parent-child dependency means that one child resource cannot exist or function properly without the parent resource. 
If such a relationship exists in a typical application scenario, return true, otherwise return false.
User will provide two resources, A and B, and three web contexts for each resource to help you understand the resource structure and their relationship.

Respond only with a single line in the following JSON format:  
{"parent-child relationship": true}  
or  
{"parent-child relationship": false}

Here are a few examples:

Example 1:  
Resource A: Post  
Resource B: Comment  
{"parent-child relationship": true}

Example 2:  
Resource A: Product  
Resource B: Post  
{"parent-child relationship": false}
        """

        conversation = [{'role':'system', 'content': system_prompt}, {'role': 'user', 'content': prompt}]
        start_time = time.time()
        try:
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=conversation,
                    response_format={
                        'type': 'json_object'
                    }
                )
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "Please wait for 1 minute before trying again" in error_msg:
                    logging.error("Rate limit exceeded: " + error_msg)
                    time.sleep(60)
                    response = None
                else:
                    logging.error("LLM API error: " + error_msg)
                    response = None

            if response is not None:
                answer = response.choices[0].message.content
            else:
                answer = "{}"
            try:
                answer = json.loads(answer)
                if answer == {}:
                    logging.warning("Failed to identify resource parent-child relationship: " + str(answer))
                elif not'parent-child relationship' in answer:
                    logging.error("Failed to identify resource parent-child relationship: " + str(answer))
                    answer = {}
            except:
                logging.error("Failed to parse response: " + str(answer))
                answer = {}
        except Exception as e:
            logging.error(f"Failed to generate response: {str(e)}")
            answer = {}
        self.total_resource_dependency_time += time.time() - start_time
        self.total_resource_dependency_count += 1
        self.resource_dependency_time += time.time() - start_time
        self.resource_dependency_count += 1
        return answer

    def identify_resource_operation_after_request(self, purpose, prompt, page_state):
        key_hash = hashlib.sha256(page_state.encode()).hexdigest()
        if key_hash in self.after_resource_operation_cache:
            logging.info("Cache hit for after_resource_operation: " + key_hash)
            return self.after_resource_operation_cache[key_hash]

        system_prompt_template = """You are a penetration testing expert. Below is a description of a web application that you 
need to analyze. The purpose of this application are {purpose}.

Previously, user asked you to analyze the semantic meaning of a request action based only on limited context — specifically, the frontend context information *before* the request action was executed. Based on that, an initial analysis result was produced.

Now, user has collected additional information *after executing the request*, including:
1. The frontend context before and after the request.
2. The full request and response data.
3. The previously inferred analysis result before execution.

Your tasks are as follows:
1. Determine whether the **previously inferred result** is correct based on the full context (before and after states + traffic data).
2. If it is **not correct**, please refine the result and give the corrected one. That is, identify the operation performed on which resource and categorize it into one of the following CRUD types: create, read, update, delete, or unknown.
3. Regardless of whether the original result is correct or not, determine whether the **request action was successfully executed**.

### Success Criteria
A request action is considered successful if:
- The frontend page state after the request action reflects the expected changes (e.g., a new item appears, an item is deleted, content is updated).
- The response data does not contain error messages or failure indicators.
- The HTTP status code is appropriate (e.g., 200, 201, 204 for success; 4xx/5xx indicates failure).

If the request action failed (e.g., due to an error message in the response, an unexpected status code, or no visible changes in the page state), it should be marked as unsuccessful.

### Handling Form Loading Requests
If the request action results in a **form being displayed**, carefully analyze its purpose:
- If it is a form for creating a new resource, the operation should be **"load create form"**, and the CRUD type should be **"read"**.
- If it is a form for modifying an existing resource, the operation should be **"load update form"**, and the CRUD type should be **"read"**.
- If it is a search or filter form, the operation should be **"load search form"**, and the CRUD type should be **"read"**.
- If the form's purpose is unclear, classify it as **"load form"**, and the CRUD type should remain **"read"**.

Note that loading a form alone does not mean an actual **create** or **update** operation has taken place. The resource should only be considered created or updated when a subsequent request submits the form and the system confirms the change.

### Resource Name Formatting
Ensure that resource names are formatted as meaningful words or phrases with spaces separating words. Avoid special characters or concatenated terms. Additionally, consider whether the request applies to **a specific resource** (singular) or **a category of resources** (plural), and format accordingly.

Please answer in the following JSON format:
{{"operation": "action", "resource": "resource type", "CRUD_type": "CRUD category", "success": true/false}}.

For example, if the request action is about deleting an order and it was successfully executed, the answer should be:
{{"operation": "delete", "resource": "order", "CRUD_type": "delete", "success": true}}.
If the request action failed, the answer should be:
{{"operation": "delete", "resource": "order", "CRUD_type": "delete", "success": false}}.

If a request action is for loading a creation form for an order, the correct response should be:
{{"operation": "load create form", "resource": "order", "CRUD_type": "read", "success": true}}.

Now, please analyze the provided information and determine the operation, resource, CRUD type, and whether the request action was successfully executed.

If you are unable to determine the operation or resource, please return {{}}
or {{"operation": "unknown", "resource": "unknown", "CRUD_type": "unknown", "success": false}}.
        """

        system_prompt = system_prompt_template.format(purpose=purpose)

        conversation = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': prompt}]

        start = time.time()
        try:
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=conversation,
                    response_format={
                        'type': 'json_object'
                    }
                )
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "Please wait for 1 minute before trying again" in error_msg:
                    logging.error("Rate limit exceeded: " + error_msg)
                    time.sleep(60)
                    response = None
                else:
                    logging.error("LLM API error: " + error_msg)
                    response = None

            if response is not None:
                answer = response.choices[0].message.content
            else:
                answer = "{}"

            try:
                answer = json.loads(answer)
                if answer == {}:
                    logging.warning("Failed to identify resource operation: " + str(answer))
                elif not 'operation' in answer or not 'resource' in answer or not 'CRUD_type' in answer or not 'success' in answer:
                    logging.error("Failed to generate resource operation: " + str(answer))
                    answer = {}
                else:
                    if 'operation' in answer and 'resource' in answer and 'CRUD_type' in answer and 'success' in answer:
                        if answer['operation'] == "unknown" or answer['resource'] == "unknown":
                            logging.warning("Unknown resource operation: " + str(answer))
                            answer = {}
            except:
                logging.error("Failed to parse response: " + str(answer))
                answer = {}
        except Exception as e:
            logging.error(f"Failed to generate response: {str(e)}")
            answer = {}
        if answer != {}:
            self.after_resource_operation_cache[key_hash] = answer
        return answer