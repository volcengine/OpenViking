"""OV recall/capture with LangChain summarization and durable LangGraph history.

Install requirements-native.txt and configure OPENAI_API_KEY (and optionally
OPENAI_BASE_URL). Reusing --thread and --database resumes the host history.
"""

import argparse

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_openai import ChatOpenAI
from langchain_openviking import OpenVikingCommitPolicy, OpenVikingContextMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from openviking_sdk import SyncHTTPClient


def build_agent(model, summary_model, client, checkpointer):
    return create_agent(
        model=model,
        middleware=[
            # Capture raw input before the next middleware can replace it.
            OpenVikingContextMiddleware(
                client=client,
                commit_policy=OpenVikingCommitPolicy(mode="always"),
            ),
            SummarizationMiddleware(
                model=summary_model,
                trigger=("messages", 20),
                keep=("messages", 6),
            ),
        ],
        checkpointer=checkpointer,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("--thread", default="native-history-demo")
    parser.add_argument("--database", default="host-history.sqlite")
    parser.add_argument("--model", default="gpt-4.1-mini")
    args = parser.parse_args()
    client = SyncHTTPClient()
    try:
        client.initialize()
        with SqliteSaver.from_conn_string(args.database) as checkpointer:
            agent = build_agent(
                ChatOpenAI(model=args.model), ChatOpenAI(model=args.model), client, checkpointer
            )
            result = agent.invoke(
                {"messages": [{"role": "user", "content": args.message}]},
                {"configurable": {"thread_id": args.thread}},
            )
            print(result["messages"][-1].content)
    finally:
        client.close()


if __name__ == "__main__":
    main()
