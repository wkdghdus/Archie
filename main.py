import My_Agent.agent as agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

def main():

    print("-"*100)
    print("아치: 안녕하세요! Hello!")

    while True:
        userInput = input("User: ")

        if userInput.lower() == "exit":
            break
        
        # Add the user's message to the conversation memory
        agent.chatHistory.append(HumanMessage(content=userInput))

        # Define context separately if needed (e.g., additional information)
        contextCombined =  ""

        # Invoke the agent with the user input and the current chat history
        response = agent.chatClient.invoke({"chat_history": agent.chatHistory, "input": userInput, "intermediate_steps": [],})

    #     debugging code
    #     print(chatHistory.buffer_as_messages)
        
    # # add chat history to firebase firestore cloud 
    # firestoreClient.add_messages(chatHistory)

main()
