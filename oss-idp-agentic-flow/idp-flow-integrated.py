from langchain_openai import ChatOpenAI
import openai
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langfuse.callback import CallbackHandler
import base64, os
from langfuse.decorators import langfuse_context, observe

from langgraph.graph import StateGraph, START, END
from typing import Annotated, List
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
import json
from langgraph.pregel import RetryPolicy


class State(TypedDict):
    messages: Annotated[list, add_messages]



def encode_image(image_path):
    """Encode image to base64 string"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
  
  

model_key="sk-fce2XjCQCitvSv0DJiru1w"
api_gateway_url="http://llama-cpp-cpu-lb-656392498.ap-southeast-2.elb.amazonaws.com"

langfuse_url="http://langfuse-lb-730705963.ap-southeast-2.elb.amazonaws.com"
local_public_key = "pk-lf-ad058c9d-da05-4814-9011-ab0197b2b41e"
local_secret_key = "sk-lf-f09f4cfa-8ede-4ff3-ab1f-f1ed0d2299a4" 

os.environ["LANGFUSE_SECRET_KEY"] = local_secret_key
os.environ["LANGFUSE_HOST"] = langfuse_url
os.environ["LANGFUSE_PUBLIC_KEY"] = local_public_key


 
# Initialize Langfuse CallbackHandler for Langchain (tracing)
langfuse_handler = CallbackHandler()


import uuid
import hashlib
import time

# Generate an 8-digit hash for run_id
def generate_run_id():
    """Generate a unique 8-digit run ID hash"""
    # Combine uuid with timestamp for uniqueness
    unique_string = f"{uuid.uuid4()}-{time.time()}"
    # Create hash and take first 8 characters
    hash_object = hashlib.md5(unique_string.encode())
    return hash_object.hexdigest()[:8]

# Generate run_id
run_id = generate_run_id()
print(f"Generated run_id: {run_id}")


client = openai.OpenAI(
    api_key=model_key,            
    base_url=api_gateway_url 
)

bc_document_user_prompt = """This is a birth certificate. Extract fields including name, date of birth, place of birth from the image.  \
                                    Extract father's first and last name and the  mother's first and last name fields. \
                                    Extract Registration Number field also. """


extraction_llm = ChatOpenAI(model="vllm-llama-3.2-vision", temperature=0.1, max_tokens=5000, api_key=model_key, base_url=api_gateway_url)
rules_llm = ChatOpenAI(model="vllm-llama-3.3", temperature=0.1, max_tokens=5000, api_key=model_key, base_url=api_gateway_url)
qwen_vllm = ChatOpenAI(model="qwen3-vllm", temperature=0.1, max_tokens=5000, api_key=model_key, base_url=api_gateway_url)
# prompts
doc_system_prompt = """You are an expert document parser. " \
                            "Extract all the fields from the supplied image and provide the information in a structured json format, with no other text or wrapper around json. " \
                            "The json will be read by machine.  Be very strict about that the output only contains JSON and nothing else. " \
                            "Do not extract any other fields which are not specified in the prompt. Be strict about it."""
doc = encode_image("se.png")
doc_user_prompt = """This is a detailed sales receipt. Extract fields including total amount due, bank account number, tax registered number and invoice number.  \
                             Extract invoice date fields. Make sure that the output is in json format. """

doc_parser_user_prompt = HumanMessage(content= [
                        {
                            "type": "text",
                            "text": doc_user_prompt,
                                    
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64," + doc
                            }
                        }
                    ])

async def extraction_node(state: State) -> State:
    return {"messages": extraction_llm.invoke([SystemMessage(doc_system_prompt), doc_parser_user_prompt] + state["messages"])}


rule_user_prompt = """Apply the following rules and give me the result in json format. \
                            1. Make sure that the Bank Account Number field is having atleast 16 characters. \
                            2. Make sure that Invoice date is not in the future. \
                            3. Make sure that Invoice date is not more than 3 months in the past. \
                            4. Check if the total due is not more than 1000. """        


rule_system_prompt = """You are an expert document rule processor which apply the provided ruleset on the given content. " \
                            "You will receive a json formatted string with different fields representing different fields in the document. " \
                            "You will act as per the instructions and rules provided in the user prompt. " \
                            "The final output will be in json format with details on the instruction and the rule description with the result."    \
                            "The json will be read by machine.  Be very strict about that the output only contains JSON and nothing else. " \
                            "Do not extract any other fields which are not specified in the instrctions or prompt. Be strict about it." \
                            "If any of the rules are not met or failed, then created a additioanl field in json with the name SUCCESS and set it to false. """  

async def rule_node(state: State) -> State:
    document_content = state["messages"][-1].content
    rule_parser_user_prompt = HumanMessage(content= [
                        {
                            "type": "text",
                            "text": rule_user_prompt + "\n The document content is as follows:\n" + document_content,
                                    
                        },
                    ])    
    return {"messages": rules_llm.invoke([SystemMessage(rule_system_prompt), rule_parser_user_prompt] + state["messages"])}




    

# Add new node for external processing
async def external_storage_node(state: State) -> State:
    """
    Node that calls external processing service
    """
    ai_messages = [msg for msg in state["messages"] if isinstance(msg, AIMessage)]
    print(f"AI Messages to Store: {json.dumps([msg.content for msg in ai_messages], indent=2)}")

    
    # [-1].content
    print(f"Data to Store {ai_messages}")
    print(json.dumps([msg.content for msg in ai_messages], indent=2))
    
    state["messages"].append(AIMessage(content="Data stored successfully!"))
    return state


def auto_process(state: State) -> State:
    messages = state["messages"]
    auto_process_message = messages[-1].content
    state["messages"].append(SystemMessage(content=auto_process_message))
    find_str = "\"SUCCESS\": false"
    if find_str in auto_process_message:  # Adjust based on desired iterations
        return "human"
    return END

async def human_node(state: State):
    state["messages"].append(AIMessage(content="Forwarded to Human for review"))
    print("Call an external API to request human input")
    return state

async def api_node(state: State):
    state["messages"].append(AIMessage(content="Forwarded to API for processing"))
    print("Call an external API to request processing")
    return state

builder = StateGraph(State)

builder.add_node("extraction", extraction_node)
builder.add_node("rule", rule_node)
builder.add_node("human", human_node)
builder.add_node("api", api_node)
# builder.add_node("store", external_storage_node, retry=RetryPolicy(max_attempts=3))

builder.add_edge(START, "extraction")
builder.add_edge("extraction", "rule")
# builder.add_edge("store", "rule")
builder.add_conditional_edges("rule", auto_process)
builder.add_edge("human", END)
builder.add_edge("api", END)



# Compile the graph with memory checkpointing
from langgraph.checkpoint.memory import MemorySaver
memory = MemorySaver()
graph = builder.compile(checkpointer=memory)

async def run_agent():
    config = {
        "configurable": {"thread_id": "1", "run_id": run_id},
        "run_name": "idp-flow-integrated",
        "callbacks": [langfuse_handler],
        "recursion_limit": 5,
    }
    async for event in graph.astream({}, config):
        print(f"-------\n{event}\n-------")
        # if "extraction" in event:
        #     print("=== extraction Report ===")
        #     print(event["extraction"]["messages"])
        #     # print(event["extraction"]["messages"][-1].content)
        #     print("\n")
        # elif "rule" in event:
        #     print("=== rule ===")
        #     print(event["rule"]["messages"])
        #     print("\n")

import asyncio
asyncio.run(run_agent())