; C++ declarations used by RepoMap-style summary extraction.
; Keep function captures scoped to real declarations/definitions so local
; object construction such as `std::vector<T> buf(n)` is not tagged as a
; function definition.

(struct_specifier
  name: (type_identifier) @name.definition.class
  body: (_)) @definition.class

(class_specifier
  name: (type_identifier) @name.definition.class) @definition.class

(declaration
  type: (union_specifier
    name: (type_identifier) @name.definition.class)) @definition.class

(enum_specifier
  name: (type_identifier) @name.definition.type) @definition.type

(type_definition
  declarator: (type_identifier) @name.definition.type) @definition.type

(declaration_list
  (alias_declaration
    name: (type_identifier) @name.definition.type)) @definition.type

(field_declaration_list
  (alias_declaration
    name: (type_identifier) @name.definition.type)) @definition.type

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name.definition.function)) @definition.function

(function_definition
  declarator: (function_declarator
    declarator: (field_identifier) @name.definition.method)) @definition.method

(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      scope: (_) @local.scope
      name: (identifier) @name.definition.method))) @definition.method

(function_definition
  declarator: (function_declarator
    declarator: (operator_name) @name.definition.method)) @definition.method

(function_definition
  declarator: (function_declarator
    declarator: (destructor_name) @name.definition.method)) @definition.method

(declaration_list
  (declaration
    declarator: (function_declarator
      declarator: (identifier) @name.definition.function))) @definition.function

(declaration_list
  (declaration
    declarator: (function_declarator
      declarator: (qualified_identifier
        scope: (_) @local.scope
        name: (identifier) @name.definition.method)))) @definition.method

(field_declaration
  declarator: (function_declarator
    declarator: (field_identifier) @name.definition.method)) @definition.method

(field_declaration
  declarator: (function_declarator
    declarator: (identifier) @name.definition.method)) @definition.method

(field_declaration
  declarator: (function_declarator
    declarator: (operator_name) @name.definition.method)) @definition.method

(field_declaration
  declarator: (function_declarator
    declarator: (destructor_name) @name.definition.method)) @definition.method
