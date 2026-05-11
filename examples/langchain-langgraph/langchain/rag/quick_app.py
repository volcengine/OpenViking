"""Deterministic LangChain RAG smoke app using OpenViking as retriever."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from openviking.integrations.langchain import InMemoryOpenVikingClient, OpenVikingRetriever


def build_app(client: InMemoryOpenVikingClient | None = None):
    client = client or InMemoryOpenVikingClient(
        {
            "viking://user/memories/preferences/deploy_color.md": (
                "The user prefers azure as the deployment color for LangChain examples."
            ),
            "viking://resources/runbooks/langchain.md": (
                "LangChain RAG apps should pass OpenViking recall into the prompt context."
            ),
        }
    )
    retriever = OpenVikingRetriever(
        client=client,
        target_uri=["viking://user/memories", "viking://resources"],
        limit=4,
        content_mode="auto",
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Answer from the supplied OpenViking context.\n\n{context}"),
            ("human", "{question}"),
        ]
    )
    model = FakeListChatModel(
        responses=[
            "OpenViking recall says the deployment color is azure.",
        ]
    )
    return (
        {
            "context": retriever | RunnableLambda(_format_docs),
            "question": RunnablePassthrough(),
        }
        | prompt
        | model
        | StrOutputParser()
    )


def _format_docs(docs) -> str:
    return "\n\n".join(f"{doc.metadata.get('openviking_uri')}\n{doc.page_content}" for doc in docs)


def main() -> str:
    app = build_app()
    answer = app.invoke("Which deployment color should the LangChain example use?")
    print(answer)
    return answer


if __name__ == "__main__":
    main()
