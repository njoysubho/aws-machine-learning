from langchain_core.tools import tool
from langchain_aws.chat_models import ChatBedrock

@tool(description="Useful for when you need to add two integers together.")
def addition(a: int, b: int) -> int:
    return a + b

@tool(description="Useful for when you need to know the weather in a city.")
def weather(city: str) -> str:
    return f"The weather in sab {city} is sunny."

model = ChatBedrock(
    provider="anthropic",
    model_id="arn:aws:bedrock:eu-west-1:xxxx:inference-profile/eu.anthropic.claude-3-5-sonnet-20240620-v1:0", 
    region="eu-west-1",)
model_with_tools = model.bind_tools([weather])
result = model_with_tools.invoke("What is the weather in Paris?")
for tool_call in result.tool_calls:
    tool_function = eval(tool_call["name"])
    result = tool_function.invoke(input=tool_call["args"])
print(result)