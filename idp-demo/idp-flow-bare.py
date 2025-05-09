from langchain_openai import ChatOpenAI
import openai
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langfuse.callback import CallbackHandler
import base64, os

def encode_image(image_path):
    """Encode image to base64 string"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
  
  

model_key="sk-fce2XjCQCitvSv0DJiru1w"
api_gateway_url="http://llama-cpp-cpu-lb-656392498.ap-southeast-2.elb.amazonaws.com"

langfuse_url="http://langfuse-lb-730705963.ap-southeast-2.elb.amazonaws.com"
local_public_key = "pk-lf-ad058c9d-da05-4814-9011-ab0197b2b41e"
local_secret_key = "sk-lf-f09f4cfa-8ede-4ff3-ab1f-f1ed0d2299a4" 

# sk-lf-396ee803-6d89-4de5-b009-2405905fd61b
# Public Key
# pk-lf-464739b6-ec72-4195-b407-96968f6c7955

os.environ["LANGFUSE_SECRET_KEY"] = local_secret_key
os.environ["LANGFUSE_HOST"] = langfuse_url
os.environ["LANGFUSE_PUBLIC_KEY"] = local_public_key


 
# Initialize Langfuse CallbackHandler for Langchain (tracing)
langfuse_handler = CallbackHandler()



client = openai.OpenAI(
    api_key=model_key,            
    base_url=api_gateway_url 
)

bc_document_user_prompt = """This is a birth certificate. Extract fields including name, date of birth, place of birth from the image.  \
                                    Extract father's first and last name and the  mother's first and last name fields. \
                                    Extract Registration Number field also. """

async def extract_doc(image_path, user_prompt):
    """Extract document fields from image using vision model"""    
    base64_image = encode_image(image_path)

    vision_model = "vllm-llama-3.2-vision"
    response = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                "role": "system",
                "content": "You are an expert document parser. " \
                            "Extract all the fields from the supplied image and provide the information in a structured json only format, no other text or wrapper around json. " \
                            "The json will be read by machine.  Be very strict about that the output only contains JSON and nothing else. " \
                            "Do not extract any other fields which are not specified in the prompt. Be strict about it." , 
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_prompt,
                                    
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]
        )    

    document_content = f"\n Extracted document Content in json:\n {response.choices[0].message.content}"
    print(document_content)
    return response.choices[0].message.content



# bc_document_content = extract_doc("bc.png", bc_document_user_prompt)

se_document_user_prompt = """This is a detailed sales receipt. Extract fields including total due, bank account number, tax registered number and invoice number.  \
                             Extract invoicde date fields."""

# se_document_content = extract_doc("se.png", se_document_user_prompt)


se_document_rule_prompt = """Apply the following rules and give me the result in json format. \
                            1. Make sure that the Bank Account Number field is having atleast 16 characters. \
                            2. Make sure that Invoice date is not in the future. \
                            3. Make sure that Invoice date is not more than 3 months in the past. \
                            4. Check if the total due is not more than 1000. """        

async def apply_doc_rules(document_content, user_prompt):
    summary_model = "vllm-llama-3.3"
    
    response = client.chat.completions.create(
            model=summary_model,
            messages=[
                {
                "role": "system",
                "content": "You are an expert document rule processor which apply the provided ruleset on the given content. " \
                            "You will receive a json formatted string with different fields representing different fields in the document. " \
                            "You will act as per the instructions and rules provided in the user prompt. " \
                            "The final output will be in json format with details on the instruction and the rule description with the result."    \
                            "The json will be read by machine.  Be very strict about that the output only contains JSON and nothing else. " \
                            "Do not extract any other fields which are not specified in the instrctions or prompt. Be strict about it." \
                            "If any of the rules are not met or failed, then created a additioanl field in json with the name SUCCESS and set it to false. " ,   
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_prompt + "\n The document content is as follows:\n" + document_content,
                                    
                        },
                    ]
                }
            ]
        )    

    document_content = f"\n Applied ruleset to the content:\n {response.choices[0].message.content}"
    print(document_content)
    return response.choices[0].message.content

# apply_doc_rules(se_document_content, se_document_rule_prompt)



from langgraph.graph import StateGraph, START, END
from typing import Annotated, List
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

class State(TypedDict):
    messages: Annotated[list, add_messages]
    



async def extraction_node(state: State) -> State:
    
    state["messages"].append(AIMessage(content=await extract_doc("se.png", se_document_user_prompt)))
    return state

async def rule_node(state: State) -> State:
    state["messages"].append(AIMessage(content=await apply_doc_rules(state["messages"][0].content, se_document_rule_prompt)))
    return state


import json
from langgraph.pregel import RetryPolicy
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
    print("Total messages in state:", len(state["messages"]))
    messages = state["messages"]
    last_message = messages[-1].content
    print(f"Last Message is: {last_message}")
    find_str = "\"SUCCESS\": false"
    if find_str in last_message:  # Adjust based on desired iterations
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
        "configurable": {"thread_id": "1"},
        "run_name": "idp-flow-bare",
        "callbacks": [langfuse_handler],
        "recursion_limit": 5,
    }
    async for event in graph.astream({}, config):
        print(f"-------\n{event}\n-------")
        if "extraction" in event:
            print("=== extraction Report ===")
            print(event["extraction"]["messages"])
            # print(event["extraction"]["messages"][-1].content)
            print("\n")
        elif "rule" in event:
            print("=== rule ===")
            print(event["rule"]["messages"])
            print("\n")

import asyncio
asyncio.run(run_agent())