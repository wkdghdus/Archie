import os
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv
import prompt

#API KEY ASSIGNMENT
_ = load_dotenv(find_dotenv())
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

#CONSTANTS/상수
GPT_MODEL = "gpt-4o"
GPT_TEMPERATURE = 0.1 #low temperature reduces possible randomness. #온도를 낮게 설정하여 무작위성을 최소화
MAX_TOKENS = 1000


def generateChat(chatHistory):

    response = client.chat.completions.create(
        model = GPT_MODEL,
        temperature = GPT_TEMPERATURE,
        messages = chatHistory
        )
    
    return response

def main():
    
    chatHistory = [{"role" : "system", "content" : prompt.systemPrompt}]

    print("enter your question: ")
    while True:
        userInput = input("You: ")
        chatHistory.append({"role": "user", "content" : userInput})
        response = generateChat(chatHistory).choices[0].message.content
        print(response)
        chatHistory.append({"role" : "assistant", "content" : response})

main()


