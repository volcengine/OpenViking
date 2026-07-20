Third-Party Notices for Aider RepoMap Queries
============================================

This directory vendors tree-sitter tag query files from Aider:

https://github.com/Aider-AI/aider/tree/main/aider/queries/tree-sitter-language-pack

Vendored revision:

5dc9490bb35f9729ef2c95d00a19ccd30c26339c

Aider is licensed under the Apache License, Version 2.0. A copy of the
license is included in this directory as `AIDER_LICENSE.txt`.

Aider's query directory states that the `.scm` files are adapted from the
GitHub repositories listed by tree-sitter-language-pack:

https://github.com/Goldziher/tree-sitter-language-pack/blob/main/sources/language_definitions.json

See the tree-sitter-language-pack project for information about the licenses
of those language repositories:

https://github.com/Goldziher/tree-sitter-language-pack/

If OpenViking modifies any vendored query file, mark the modified file or this
notice with a short description of the change.

OpenViking local extensions:

- `tree-sitter-language-pack/typescript-tags.scm` is adapted from Aider's
  JavaScript tag query to preserve TypeScript skeleton extraction.
- `tree-sitter-language-pack/php-tags.scm` was added to preserve PHP skeleton
  extraction.
