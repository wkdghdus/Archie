
from typing import Annotated, List

from dotenv import load_dotenv
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import MessagesPlaceholder, ChatPromptTemplate
from langchain_core.tools import create_retriever_tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone

import prompt

#load env variables
load_dotenv()

####--------CONSTANTS/상수--------####
#For OpenAI ChatGPT
GPT_MODEL = "gpt-4o"
GPT_TEMPERATURE = 0.1 #low temperature reduces possible randomness. #온도를 낮게 설정하여 무작위성을 최소화
MAX_TOKENS = 1000

#For Firebase Firestore
PROJECT_ID = "archie-fff03"
SESSION_ID = "user_session_1"
COLLECTION_NAME = "chat_history"

#for pinecone vector store
PC = Pinecone()
VECTORSTORE_INDEX_NAME = "products"
VECTORSTORE_INDEX = PC.Index(VECTORSTORE_INDEX_NAME)
print(VECTORSTORE_INDEX.describe_index_stats())

####--------CONSTANTS/상수 ends--------####


####--------initialization--------####
print("initializing openAI GPT")
#Init OpenAI model
openAIClient = ChatOpenAI(model = GPT_MODEL, temperature = GPT_TEMPERATURE, max_tokens = MAX_TOKENS)
embeddings = OpenAIEmbeddings(model="text-embedding-ada-002")
print("initializing finished")


#init pincone/vector store 
print("Initializing vectorstore")
db = PineconeVectorStore(index= VECTORSTORE_INDEX, embedding=embeddings)
print("Initializing Finished")

sourceList = []    # list for needed documents (in chunks)
chatHistory = []    # list for chat history

###########for cloud chat history storing ##########
# print("initializing chat history")
# cloudChatHistory = FirestoreChatMessageHistory(
#     session_id = SESSION_ID,
#     collection = COLLECTION_NAME,
#     client = firestoreClient,
# )
# print("initializing Finished")
###########for cloud chat history storing ##########

####--------initialization ends--------####


####--------Functions--------####

# # Retrieve the chat history from FirestoreChatMessageHistory and convert to list of base messages
# def getCloudChatHistory(cloudChatHistory):
#     # Initialize a ConversationBufferMemory
#     memory = ConversationBufferMemory(return_messages=True)

#     # Iterate over messages from cloud history and add to memory
#     for entry in cloudChatHistory.messages:
#         if entry["role"] == "user":
#             memory.chat_memory.add_message(HumanMessage(content=entry["content"]))
#         elif entry["role"] == "assistant":
#             memory.chat_memory.add_message(AIMessage(content=entry["content"]))

#     # Return the memory instance with all messages added
#     return memory
@tool("productSearch")
def getRelevantSources(newInput):
    """use this tool to search product"""
    ####--------Creating vector store retrievor--------####

    #vector store retriever
    #connected to scientific data vector db
    retriever = db.as_retriever()


    return retriever.invoke(input=newInput) #파란 텍스트 부분

def listToString(docList):
    ret = ""
    for doc in docList:
        ret = ret + doc
    
    return ret


def getSourceList():
    return sourceList


retriever = db.as_retriever()
retriever_tool = create_retriever_tool(
    retriever,
    name="productSearch",  # 도구의 이름을 입력합니다.
    description="use this tool to search product",  # 도구에 대한 설명을 자세히 기입해야 합니다!!
)


@tool("generatefinaloutput")
def generateFinalOutput():
    """generates final output for the user"""

    #code to generate output 

    finalOutput = sourceList

    return finalOutput


####--------tools for the agent--------####
tools = [
    retriever_tool

]
####--------tools end--------####


####--------creating agent--------####

# creating prompt template
agentSystemPrompt = ChatPromptTemplate.from_messages(
    [
        ("system", prompt.reactAgentPrompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        ("placeholder","{tool_names}")
    ]
)

agent = create_tool_calling_agent(

    llm = openAIClient,
    tools = tools,
    prompt = agentSystemPrompt,
)

agent_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=tools, handle_parsing_errors=True, verbose=True, 
)
####--------creating agent ends--------####


def main():

    print("-"*100)
    print("아치: 안녕하세요! Hello!")

    while True:
        userInput = input("User: ")

        if userInput.lower() == "exit":
            break
        
        # Add the user's message to the conversation memory
        chatHistory.append(HumanMessage(content=userInput))

        # Define context separately if needed (e.g., additional information)
        contextCombined =  ""

        # Invoke the agent with the user input and the current chat history
        response = agent_executor.invoke({"chat_history": chatHistory, "context": contextCombined, "input": userInput})
        print("아치:", response["output"])

        # Add the agent's response to the conversation memory
        chatHistory.append(AIMessage(content=response["output"]))

        #debugging code
        #print(chatHistory.buffer_as_messages)
        
    # # add chat history to firebase firestore cloud 
    # firestoreClient.add_messages(chatHistory)

main()


