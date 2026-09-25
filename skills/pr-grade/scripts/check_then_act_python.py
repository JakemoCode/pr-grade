"""Python adapter for check_then_act.py: the same per-function syntax facts the TypeScript adapter
emits, read with the standard library's `ast`. Purely syntactic; every judgment stays in the core."""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
LOOPS = (ast.For, ast.AsyncFor, ast.While)
BUILTINS = set(dir(builtins))
COLLECTION_CALLS = {'dict', 'list', 'set', 'tuple', 'defaultdict', 'OrderedDict', 'Counter', 'deque'}
TRIES = tuple(getattr(ast, name) for name in ('Try', 'TryStar') if hasattr(ast, name))
COLLECTION_LITERALS =(ast.List, ast.Dict, ast.Set, ast.Tuple, ast.ListComp, ast.DictComp, ast.SetComp)


def dotted(node) -> str:
    """A dotted name for a callee, with calls dropped: self.repo.for_run(x).find is self.repo.for_run.find."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{dotted(node.value)}.{node.attr}'
    if isinstance(node, ast.Subscript):
        return f'{dotted(node.value)}[]'
    if isinstance(node, ast.Call):
        return dotted(node.func)
    if isinstance(node, ast.Await):
        return dotted(node.value)
    return '?'


def sql_of(call) -> str | None:
    """'write' or 'read' when a string argument anywhere down the call chain starts with SQL that does."""
    current = call
    while isinstance(current, ast.Call):
        for argument in [*current.args, *(k.value for k in current.keywords)]:
            literal = None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                literal = argument.value
            elif isinstance(argument, ast.JoinedStr) and argument.values:
                head = argument.values[0]
                literal = head.value if isinstance(head, ast.Constant) and isinstance(head.value, str) else None
            if literal is not None and literal.strip():
                word = literal.split()[0].lower()
                if word in ('insert', 'update', 'delete', 'replace', 'upsert', 'merge'):
                    return 'write'
                if word in ('select', 'with'):
                    return 'read'
        current = current.func.value if isinstance(current.func, (ast.Attribute, ast.Subscript)) else None
    return None


def exit_kind(body: list) -> str | None:
    """'function' when every path through the statements leaves the function, 'loop' when every path at
    least leaves the enclosing loop, otherwise None."""
    if not body:
        return None
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return 'function'
    if isinstance(last, (ast.Break, ast.Continue)):
        return 'loop'
    if isinstance(last, ast.If):
        then, otherwise = exit_kind(last.body), exit_kind(last.orelse)
        if not then or not otherwise:
            return None
        return 'function' if then == otherwise == 'function' else 'loop'
    return None


class _File:
    def __init__(self, text: str, tree: ast.AST):
        self.lines = text.splitlines()
        # Every name the file defines or imports at any depth, so a def set() is not taken for the builtin.
        self.bound = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                                 ast.ClassDef))}
        self.bound |= {(alias.asname or alias.name).split('.')[0] for node in ast.walk(tree)
                       if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

    def span(self, node) -> list:
        return [[node.lineno, node.col_offset], [node.end_lineno, node.end_col_offset]]

    def text(self, node) -> str:
        line = self.lines[node.lineno - 1].encode()[node.col_offset:].decode(errors='replace').strip()
        return f'{line[:97]}...' if len(line) > 100 else line

    def class_name(self, node) -> str:
        parent = self.parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.ClassDef):
                return parent.name
            if isinstance(parent, FUNCTIONS):
                return ''
            parent = self.parents.get(parent)
        return ''

    def loop_end(self, node) -> list | None:
        """Where a guard that exits by break or continue stops covering: the end of its loop. Python has no
        labels and no switch, so every such jump leaves the nearest loop."""
        parent = self.parents.get(node)
        while parent is not None and not isinstance(parent, FUNCTIONS):
            if isinstance(parent, LOOPS):
                return [parent.end_lineno, parent.end_col_offset]
            parent = self.parents.get(parent)
        return None

    def reach_end(self, node) -> list | None:
        """The end of the innermost statement list around an await that always leaves the function (or of
        the return or raise holding it): nothing after it is reachable from the await."""
        child, parent = node, self.parents.get(node)
        while parent is not None and not isinstance(child, FUNCTIONS):
            if isinstance(parent, (ast.Return, ast.Raise)):
                return [parent.end_lineno, parent.end_col_offset]
            for field in ('body', 'orelse', 'finalbody', 'handlers'):
                statements = getattr(parent, field, None)
                if isinstance(statements, list) and child in statements and exit_kind(statements) == 'function':
                    return [statements[-1].end_lineno, statements[-1].end_col_offset]
            child, parent = parent, self.parents.get(parent)
        return None

    def is_chained_receiver(self, call) -> bool:
        """The call is the receiver of another call: conn.execute(sql) in conn.execute(sql).fetchone()."""
        parent = self.parents.get(call)
        return (isinstance(parent, ast.Attribute) and parent.value is call
                and isinstance(self.parents.get(parent), ast.Call) and self.parents[parent].func is parent)

    def is_inline_callback(self, fn) -> bool:
        parent = self.parents.get(fn)
        return isinstance(parent, ast.Call) and (fn in parent.args or any(k.value is fn for k in parent.keywords))

    def name_of(self, fn) -> str:
        cls = self.class_name(fn)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return f'{cls}.{fn.name}' if cls else fn.name
        parent = self.parents.get(fn)
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            return parent.targets[0].id
        if self.is_inline_callback(fn):
            enclosing = self.parents.get(parent)
            while enclosing is not None and not isinstance(enclosing, FUNCTIONS):
                enclosing = self.parents.get(enclosing)
            host = self.name_of(enclosing) if enclosing is not None else '<module>'
            return f'{host} > {dotted(parent.func)} callback'
        return f'<anonymous>@{fn.lineno}'


def _analyze_function(f: _File, fn) -> dict:
    ir = {'name': f.name_of(fn), 'span': f.span(fn), 'isConstructor': getattr(fn, 'name', '') == '__init__',
          'inlineCallee': dotted(f.parents[fn].func) if f.is_inline_callback(fn) else None,
          # A decorator wraps the whole body the way a callback's host does: @transaction.atomic, @locked.
          'wrappers': [dotted(d) for d in getattr(fn, 'decorator_list', [])],
          'calls': [], 'awaits': [], 'assigns': [], 'conds': [], 'memberWrites': [], 'scopes': []}
    ids: dict[ast.AST, int] = {}

    def id_of(call) -> int:
        return ids.setdefault(call, len(ids))

    def refs_of(root) -> list[dict]:
        """Everything an expression references: calls, local names (with the calls whose arguments hold
        them), and self.x members. Nested functions are opaque."""
        refs: list[dict] = []

        def walk(node, via):
            if node is not root and isinstance(node, FUNCTIONS):
                return
            if isinstance(node, ast.Call):
                refs.append({'t': 'call', 'id': id_of(node)})
                walk(node.func, via)
                for argument in [*node.args, *(k.value for k in node.keywords)]:
                    walk(argument, [*via, id_of(node)])
                return
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == 'self':
                    refs.append({'t': 'member', 'path': f'self.{node.attr}'})
                walk(node.value, via)
                return
            if isinstance(node, ast.Name):
                refs.append({'t': 'name', 'name': node.id, 'via': via})
                return
            for child in ast.iter_child_nodes(node):
                walk(child, via)

        walk(root, [])
        return refs

    def names_of(target) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for element in target.elts for name in names_of(element)]
        if isinstance(target, ast.Starred):
            return names_of(target.value)
        return []

    def is_collection(value) -> bool:
        return isinstance(value, COLLECTION_LITERALS) or (
            isinstance(value, ast.Call) and dotted(value.func).rsplit('.', 1)[-1] in COLLECTION_CALLS)

    def member_write(target, node, ctx):
        """self.x for any target rooted at self: self.x, self.x.y, self.x[k]."""
        parts = dotted(target).split('.')
        if parts[0] == 'self' and len(parts) > 1:
            ir['memberWrites'].append({'path': f"self.{parts[1].replace('[]', '')}", 'span': f.span(node),
                                       'line': node.lineno, 'inHandler': ctx['inHandler'], 'inline': ctx['inline'],
                                       'scopeIds': ctx['scopeIds']})

    def assign(names, node, value, ctx):
        if names:
            ir['assigns'].append({'names': names, 'span': f.span(node), 'line': node.lineno, 'refs': refs_of(value),
                                  'collection': is_collection(value), 'inline': ctx['inline']})

    def visit(node, ctx):
        if node is not fn and isinstance(node, FUNCTIONS):
            return
        if isinstance(node, ast.Call):
            callee, chained = dotted(node.func), f.is_chained_receiver(node)
            # set(...) is the builtin, not a helper named like a write, unless the module rebinds the name.
            if callee in BUILTINS and callee not in f.bound:
                callee = f'builtins.{callee}'
            ir['calls'].append({
                'id': id_of(node), 'callee': callee, 'span': f.span(node), 'line': node.lineno, 'text': f.text(node),
                'awaited': isinstance(f.parents.get(node), ast.Await), 'bare': '.' not in callee,
                'inHandler': ctx['inHandler'], 'inline': ctx['inline'], 'chainedReceiver': chained,
                'sql': None if chained else sql_of(node), 'scopeIds': ctx['scopeIds']})
        elif isinstance(node, ast.Await):
            ir['awaits'].append({'span': f.span(node), 'line': node.lineno, 'kind': 'await', 'reachEnd': f.reach_end(node),
                                 'text': f.text(node), 'inline': ctx['inline'], 'scopeIds': ctx['scopeIds']})
        elif isinstance(node, ast.AsyncFor):
            ir['awaits'].append({'span': f.span(node.iter), 'line': node.lineno, 'kind': 'for-await',
                                 'reachEnd': f.reach_end(node),
                                 'text': f.text(node), 'inline': ctx['inline'], 'scopeIds': ctx['scopeIds']})
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                context = item.context_expr
                # A with block is not a callback: what runs inside it runs in line with the code around it.
                ir['scopes'].append({'id': len(ir['scopes']), 'kind': 'with', 'callee': dotted(context),
                                     'callId': id_of(context) if isinstance(context, ast.Call) else None,
                                     'span': f.span(node), 'line': node.lineno})
                if isinstance(node, ast.AsyncWith):
                    # async with waits to enter: taking a lock is itself a gap for a check made before it.
                    ir['awaits'].append({'span': f.span(context), 'line': node.lineno, 'kind': 'await',
                                         'reachEnd': f.reach_end(node),
                                         'text': f.text(node), 'inline': ctx['inline'], 'scopeIds': ctx['scopeIds']})
                if item.optional_vars is not None:
                    assign(names_of(item.optional_vars), node, context, ctx)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    member_write(target, node, ctx)
            # Only a plain assignment binds a local to what a read returned.
            if isinstance(node, ast.Assign) or (isinstance(node, ast.AnnAssign) and node.value is not None):
                assign([n for target in targets for n in names_of(target)], node, node.value, ctx)
        elif isinstance(node, ast.NamedExpr):
            assign(names_of(node.target), node, node.value, ctx)
        elif isinstance(node, ast.If):
            exits = exit_kind(node.body)
            ir['conds'].append({'kind': 'if', 'span': f.span(node), 'condSpan': f.span(node.test), 'line': node.lineno,
                                'guard': exits is not None,
                                'guardEnd': f.loop_end(node) if exits == 'loop' else None, 'inline': ctx['inline'],
                                'inHandler': ctx['inHandler'], 'refs': refs_of(node.test), 'text': f.text(node)})
        elif isinstance(node, ast.IfExp):
            ir['conds'].append({'kind': 'ternary', 'span': f.span(node), 'condSpan': f.span(node.test),
                                'line': node.lineno, 'guard': False, 'inline': ctx['inline'],
                                'inHandler': ctx['inHandler'], 'refs': refs_of(node.test), 'text': f.text(node)})
        if isinstance(node, TRIES):
            for statement in [*node.body, *node.orelse, *node.finalbody]:
                visit(statement, ctx)
            # finally and else run on the normal path; only the handlers are failure bookkeeping.
            for handler in node.handlers:
                visit(handler, {**ctx, 'inHandler': True})
            return
        for child in ast.iter_child_nodes(node):
            visit(child, ctx)

    ctx = {'inline': False, 'inHandler': False, 'scopeIds': []}
    for statement in (fn.body if isinstance(fn.body, list) else [fn.body]):
        visit(statement, ctx)
    return ir


def parse_python(files: list[str], root: Path) -> tuple[dict, list[dict]]:
    """Each file's functions, and the files that could not be read."""
    parsed, skipped = {}, []
    for file in files:
        try:
            text = (root / file).read_text(encoding='utf-8')
            tree = ast.parse(text, filename=file)
        except SyntaxError as error:
            version = '.'.join(map(str, sys.version_info[:2]))
            skipped.append({'file': file, 'reason': f'python {version} cannot parse it (line {error.lineno}: '
                                                    f'{error.msg}); run check_then_act.py with a newer python3'})
            continue
        except (UnicodeDecodeError, ValueError) as error:
            skipped.append({'file': file, 'reason': f'cannot read it: {error}'})
            continue
        f = _File(text, tree)
        functions = sorted((node for node in ast.walk(tree) if isinstance(node, FUNCTIONS)),
                           key=lambda node: (node.lineno, node.col_offset))
        parsed[file] = [_analyze_function(f, node) for node in functions]
    return parsed, skipped
