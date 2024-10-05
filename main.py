import os
import prompt
from openai import OpenAI
from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from google.cloud import firestore
from langchain_google_firestore import FirestoreChatMessageHistory
from langchain.chains.history_aware_retriever import create_history_aware_retriever
from langchain.chains.retrieval import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import MessagesPlaceholder, ChatPromptTemplate
from langchain_core.tools import Tool
from langchain.agents import AgentExecutor, create_react_agent
from pinecone import Pinecone, ServerlessSpec
from langchain_pinecone import PineconeVectorStore



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
VECTORSTORE_INDEX_NAME = "archie"
####--------CONSTANTS/상수 ends--------####


####--------initialization--------####
print("initializing openAI GPT")
#Init OpenAI model
openAIClient = ChatOpenAI(model = GPT_MODEL, temperature = GPT_TEMPERATURE, max_tokens = MAX_TOKENS)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
print("initializing finished")

print("initializing firestore")
#Init Firebase history storage
firestoreClient = firestore.Client(project = PROJECT_ID)
print("initializing Finished")

print("initializing chat history")
cloudChatHistory = FirestoreChatMessageHistory(
    session_id = SESSION_ID,
    collection = COLLECTION_NAME,
    client = firestoreClient,
)
print("initializing Finished")

#init pincone/vector store 
print("Initializing vectorstore")
db = PineconeVectorStore(index= VECTORSTORE_INDEX_NAME, embedding=embeddings)

print("Initializing Finished")
####--------initialization ends--------####


def getRelevantOutput(newInput, chatHistory, answerList):
    ####--------Creating vector store retrievor--------####

    #vector store retriever
    #connected to scientific data vector db
    retriever = db.as_retriever()

    # Contextualize question prompt
    # This system prompt helps the AI understand that it should reformulate the question
    # based on the chat history to make it a standalone question
    contextualizeQSystemPrompt = prompt.contextual_q_system_prompt

    # Create a prompt template for contextualizing questions
    contextualize_q_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualizeQSystemPrompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )

    # Create a history-aware retriever
    # This uses the LLM to help reformulate the question based on chat history
    history_aware_retriever = create_history_aware_retriever(
        openAIClient, retriever, contextualize_q_prompt
    )
    ####--------Retriever creation ends--------####

    ####--------Creating question answering chain--------####
    # Answer question prompt
    # This system prompt helps the AI understand that it should provide concise answers
    # based on the retrieved context and indicates what to do if the answer is unknown
    qaSystemPrompt = prompt.qa_system_prompt

    # Create a prompt template for answering questions
    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", qaSystemPrompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )

    # Create a chain to combine documents for question answering
    # `create_stuff_documents_chain` feeds all retrieved context into the LLM
    question_answer_chain = create_stuff_documents_chain(openAIClient, qa_prompt)

    # Create a retrieval chain that combines the history-aware retriever and the question answering chain
    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)

    response = rag_chain.invoke({"input": newInput, "chat_history": chatHistory})

    #append to the answer List
    answerList.append({response['answer']})

    return answerList

def listToString(docList):
    ret = ""

    for doc in docList:
        ret = ret + doc
    
    return ret


tools = [
    Tool(
        name="genenrate relevant insight",
        func=getRelevantOutput,
        description="appends the specific advice for the latest question to a list",
    ),
    Tool(
        name="returns the list as a string",
        func=listToString,
        description="returns a string output when list is given"
    ),
]



# Create the ReAct Agent with document store retriever
agent = create_react_agent(
    llm=openAIClient,
    tools=tools,
    prompt=prompt.AgentSystemPrompt,
)

agent_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=tools, handle_parsing_errors=True, verbose=True,
)

def main():
    
    chatHistory = cloudChatHistory 

    while True:
        query = input("You: ")
        if query.lower() == "exit":
            break
        response = agent_executor.invoke(
            {"input": query, "chat_history": chatHistory})
        print(f"AI: {response['output']}")

        # Update history
        chatHistory.append(HumanMessage(content=query))
        chatHistory.append(AIMessage(content=response["output"]))
        
        # add chat history to firebase firestore cloud 
        firestoreClient.add_messages(chatHistory)

main()


