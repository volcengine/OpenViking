; OpenViking local extension, adapted from Aider's JavaScript tag query.

(method_definition name: (property_identifier) @name.definition.method) @definition.method
([
  (class name: (_) @name.definition.class)
  (class_declaration name: (_) @name.definition.class)
] @definition.class)
([
  (function_expression name: (identifier) @name.definition.function)
  (function_declaration name: (identifier) @name.definition.function)
  (generator_function name: (identifier) @name.definition.function)
  (generator_function_declaration name: (identifier) @name.definition.function)
] @definition.function)
(lexical_declaration
  (variable_declarator
    name: (identifier) @name.definition.function
    value: [(arrow_function) (function_expression)]) @definition.function)
