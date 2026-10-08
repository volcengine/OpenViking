"""Business selection for account-scoped reranking."""


def select_rerank(view):
    return view.account.rerank if view.account.rerank is not None else view.cluster.rerank
