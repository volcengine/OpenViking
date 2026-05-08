export const OPENVIKING_RECALL_TOOL_NAME = "openviking_recall" as const;
export const OPENVIKING_RECALL_TOOL_REFERENCE_NAME = "openvikingRecall" as const;
export const OPENVIKING_RECALL_TOOL_DISPLAY_NAME = "OpenViking Recall" as const;
export const OPENVIKING_RECALL_TOOL_USER_DESCRIPTION = "Retrieve relevant OpenViking memories for the current coding task." as const;

export const OPENVIKING_RECALL_TOOL_DESCRIPTION = [
  "Retrieve only previously stored OpenViking long-term memory that is relevant to the user's current coding question, task, debugging context, design decision, repository convention, personal preference, or past project history.",
  "Use this tool before answering when the prompt asks what was decided before, refers to prior work, asks about repo-specific conventions, mentions preferences, asks about dates or events, or depends on context that may have been saved outside the current chat.",
  "Do not use this tool for generic programming knowledge, questions fully answered by files already open in the workspace, simple calculations, chit-chat, or prompts that clearly do not require remembered context.",
  "The tool returns a ranked, token-budgeted <openviking-context> block with inline memory snippets and viking:// URIs that can be expanded with the OpenViking read tool when more detail is needed.",
  "Pass a concise search query containing the user's key nouns, repository names, decisions, preferences, errors, or feature names; avoid copying the entire prompt when a shorter query captures the recall need.",
].join(" ");
