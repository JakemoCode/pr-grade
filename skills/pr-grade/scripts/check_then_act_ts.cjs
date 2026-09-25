#!/usr/bin/env node
'use strict';
// TypeScript and JavaScript adapter for check_then_act.py. Reads {root, files} as JSON on stdin and
// writes one record of syntax facts per function to stdout. It is purely syntactic: it uses
// createSourceFile only, never reads config, and leaves every judgment to the Python core.
//
// The compiler comes from the repository being scanned, or from PR_GRADE_TYPESCRIPT. Without one it
// exits 3, and the core skips TypeScript files with a notice. The plugin itself ships no npm package.

const fs = require('fs');
const path = require('path');

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(3);
}

// The repository's own compiler first, then PR_GRADE_TYPESCRIPT. A build without the JavaScript
// compiler API (createSourceFile) is passed over, so the environment can stand in for it.
function loadTypeScript(root) {
  const candidates = [];
  try {
    candidates.push(require.resolve('typescript', { paths: [root] }));
  } catch (_) {
    // Not installed in the repository; fall back to the environment.
  }
  const fromEnv = process.env.PR_GRADE_TYPESCRIPT;
  if (fromEnv) candidates.push(fromEnv, path.join(fromEnv, 'lib', 'typescript.js'));
  const unusable = [];
  for (const candidate of candidates) {
    let loaded;
    try {
      loaded = require(candidate);
    } catch (_) {
      continue;
    }
    if (typeof loaded.createSourceFile === 'function') return { ts: loaded, from: candidate };
    unusable.push(`${candidate} (typescript ${loaded.version} has no createSourceFile)`);
  }
  const passedOver = unusable.length ? `; passed over ${unusable.join(', ')}` : '';
  fail(`no typescript package found from ${root}${passedOver}; set PR_GRADE_TYPESCRIPT to one`);
}

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const { ts, from } = loadTypeScript(input.root);

const K = ts.SyntaxKind;
const FUNCTION_EXITS = new Set([K.ReturnStatement, K.ThrowStatement]);
const LOOP_EXITS = new Set([K.ContinueStatement, K.BreakStatement]);
const ASSIGNMENT_OPERATORS = new Set([
  K.EqualsToken, K.PlusEqualsToken, K.MinusEqualsToken, K.AsteriskEqualsToken, K.SlashEqualsToken,
  K.PercentEqualsToken, K.AmpersandEqualsToken, K.BarEqualsToken, K.CaretEqualsToken,
  K.QuestionQuestionEqualsToken, K.BarBarEqualsToken, K.AmpersandAmpersandEqualsToken,
]);
const COLLECTION_CONSTRUCTORS = new Set(['Map', 'Set', 'WeakMap', 'WeakSet', 'Array', 'Object']);

function scriptKind(file) {
  const ext = path.extname(file);
  if (ext === '.tsx') return ts.ScriptKind.TSX;
  if (ext === '.jsx') return ts.ScriptKind.JSX;
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs') return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function isFunctionLike(node) {
  return ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isArrowFunction(node)
    || ts.isFunctionExpression(node) || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)
    || ts.isConstructorDeclaration(node);
}

function unwrap(node) {
  while (node && (ts.isParenthesizedExpression(node) || ts.isNonNullExpression(node) || ts.isAsExpression(node)
    || (ts.isTypeAssertionExpression && ts.isTypeAssertionExpression(node))
    || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(node)))) {
    node = node.expression;
  }
  return node;
}

// The parent, skipping parentheses, non-null assertions and type assertions.
function outer(node) {
  let parent = node.parent;
  while (parent && (ts.isParenthesizedExpression(parent) || ts.isNonNullExpression(parent) || ts.isAsExpression(parent)
    || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(parent)))) {
    parent = parent.parent;
  }
  return parent;
}

// A dotted name for a callee, with calls dropped: options.gates.forExecution(x).find(y) is
// options.gates.forExecution.find.
function dotted(node) {
  node = unwrap(node);
  if (!node) return '?';
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) return node.text;
  if (node.kind === K.ThisKeyword) return 'this';
  if (node.kind === K.SuperKeyword) return 'super';
  if (ts.isPropertyAccessExpression(node)) return `${dotted(node.expression)}.${node.name.text}`;
  if (ts.isElementAccessExpression(node)) return `${dotted(node.expression)}[]`;
  if (ts.isCallExpression(node) || ts.isAwaitExpression(node)) return dotted(node.expression);
  return '?';
}

function isInlineCallback(fn) {
  const parent = fn.parent;
  return Boolean(parent && (ts.isCallExpression(parent) || ts.isNewExpression(parent))
    && parent.arguments && parent.arguments.includes(fn));
}

// 'function' when every path through the statement leaves the function, 'loop' when every path at
// least leaves the enclosing loop or switch (break, continue), otherwise null.
function exitKind(statement) {
  if (!statement) return null;
  if (FUNCTION_EXITS.has(statement.kind)) return 'function';
  if (LOOP_EXITS.has(statement.kind)) return 'loop';
  if (ts.isBlock(statement)) return exitKind(statement.statements[statement.statements.length - 1]);
  if (ts.isIfStatement(statement)) {
    const then = exitKind(statement.thenStatement);
    const otherwise = exitKind(statement.elseStatement);
    if (!then || !otherwise) return null;
    return then === 'function' && otherwise === 'function' ? 'function' : 'loop';
  }
  return null;
}

function isLoop(node) {
  return ts.isForStatement(node) || ts.isForOfStatement(node) || ts.isForInStatement(node)
    || ts.isWhileStatement(node) || ts.isDoStatement(node);
}

// The break and continue statements that end the paths exitKind followed.
function terminalJumps(statement) {
  if (!statement) return [];
  if (LOOP_EXITS.has(statement.kind)) return [statement];
  if (ts.isBlock(statement)) return terminalJumps(statement.statements[statement.statements.length - 1]);
  if (ts.isIfStatement(statement)) {
    return [...terminalJumps(statement.thenStatement), ...terminalJumps(statement.elseStatement)];
  }
  return [];
}

// The statement a jump leaves: its label's statement, else the nearest loop for continue, else the
// nearest loop or switch for break.
function jumpTarget(jump) {
  for (let parent = jump.parent; parent && !ts.isFunctionLike(parent); parent = parent.parent) {
    if (jump.label) {
      if (ts.isLabeledStatement(parent) && parent.label.text === jump.label.text) return parent.statement;
    } else if (isLoop(parent) || (ts.isBreakStatement(jump) && ts.isSwitchStatement(parent))) {
      return parent;
    }
  }
  return null;
}

function propertyName(name) {
  if (!name) return '?';
  if (ts.isIdentifier(name) || ts.isPrivateIdentifier(name) || ts.isStringLiteral(name)) return name.text;
  return '?';
}

function className(node) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if ((ts.isClassDeclaration(parent) || ts.isClassExpression(parent)) && parent.name) return parent.name.text;
  }
  return '';
}

function analyzeFile(file, root) {
  const text = fs.readFileSync(path.join(root, file), 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, scriptKind(file));
  const pos = (offset) => {
    const lc = sf.getLineAndCharacterOfPosition(offset);
    return [lc.line + 1, lc.character];
  };
  const spanOf = (node) => [pos(node.getStart(sf)), pos(node.getEnd())];
  const lineOf = (node) => pos(node.getStart(sf))[0];
  const textOf = (node) => {
    const first = node.getText(sf).split('\n')[0].trim();
    return first.length > 100 ? `${first.slice(0, 97)}...` : first;
  };

  function nameOf(fn) {
    const cls = className(fn);
    const qualify = (name) => (cls ? `${cls}.${name}` : name);
    if (ts.isConstructorDeclaration(fn)) return qualify('constructor');
    if (ts.isMethodDeclaration(fn) || ts.isGetAccessorDeclaration(fn) || ts.isSetAccessorDeclaration(fn)) {
      return qualify(propertyName(fn.name));
    }
    if (ts.isFunctionDeclaration(fn) && fn.name) return fn.name.text;
    const parent = fn.parent;
    if (parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) return parent.name.text;
    if (parent && (ts.isPropertyAssignment(parent) || ts.isPropertyDeclaration(parent))) return qualify(propertyName(parent.name));
    if (isInlineCallback(fn)) {
      let enclosing = parent.parent;
      while (enclosing && !isFunctionLike(enclosing)) enclosing = enclosing.parent;
      const host = enclosing ? nameOf(enclosing) : '<module>';
      return `${host} > ${dotted(parent.expression)} callback`;
    }
    return `<anonymous>@${lineOf(fn)}`;
  }

  function analyzeFunction(fn) {
    const ir = {
      name: nameOf(fn),
      span: spanOf(fn),
      isConstructor: ts.isConstructorDeclaration(fn),
      inlineCallee: isInlineCallback(fn) ? dotted(fn.parent.expression) : null,
      calls: [], awaits: [], assigns: [], conds: [], memberWrites: [], scopes: [],
    };
    const ids = new Map();
    const idOf = (call) => {
      if (!ids.has(call)) ids.set(call, ids.size);
      return ids.get(call);
    };

    // this.x for any access rooted at this: this.x, this.x.y, this.x[k].
    function memberPath(node) {
      const parts = dotted(node).split('.');
      return parts[0] === 'this' && parts.length > 1 ? `this.${parts[1].replace('[]', '')}` : null;
    }

    function isTypePosition(identifier) {
      const parent = identifier.parent;
      return Boolean(parent && (ts.isTypeReferenceNode(parent) || ts.isTypeQueryNode(parent)
        || ts.isExpressionWithTypeArguments(parent) || ts.isQualifiedName(parent)));
    }

    // Everything an expression references: calls, local names (with the calls whose arguments
    // hold them), and this.x members. Nested functions are opaque.
    function refsOf(rootNode) {
      const refs = [];
      (function walk(node, via) {
        if (!node) return;
        if (node !== rootNode && isFunctionLike(node)) return;
        if (ts.isCallExpression(node)) {
          refs.push({ t: 'call', id: idOf(node) });
          walk(node.expression, via);
          for (const argument of node.arguments) walk(argument, [...via, idOf(node)]);
          return;
        }
        if (ts.isPropertyAccessExpression(node)) {
          const member = memberPath(node);
          if (member && unwrap(node.expression).kind === K.ThisKeyword) refs.push({ t: 'member', path: member });
          walk(node.expression, via);
          return;
        }
        if (ts.isIdentifier(node)) {
          const parent = node.parent;
          const isKey = parent && ts.isPropertyAssignment(parent) && parent.name === node;
          if (!isKey && !isTypePosition(node)) refs.push({ t: 'name', name: node.text, via });
          return;
        }
        ts.forEachChild(node, (child) => walk(child, via));
      })(rootNode, []);
      return refs;
    }

    function sqlOf(call) {
      for (let current = call; current && ts.isCallExpression(current);) {
        for (const argument of current.arguments) {
          let literal = null;
          if (ts.isStringLiteral(argument) || ts.isNoSubstitutionTemplateLiteral(argument)) literal = argument.text;
          else if (ts.isTemplateExpression(argument)) literal = argument.head.text;
          if (literal !== null) {
            const word = literal.trim().split(/\s+/)[0].toLowerCase();
            if (['insert', 'update', 'delete', 'replace', 'upsert', 'merge'].includes(word)) return 'write';
            if (word === 'select' || word === 'with') return 'read';
          }
        }
        const callee = unwrap(current.expression);
        current = callee && (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee))
          ? unwrap(callee.expression) : null;
      }
      return null;
    }

    function isChainedReceiver(call) {
      const parent = outer(call);
      return Boolean(parent && (ts.isPropertyAccessExpression(parent) || ts.isElementAccessExpression(parent))
        && unwrap(parent.expression) === call && parent.parent && ts.isCallExpression(parent.parent)
        && unwrap(parent.parent.expression) === parent);
    }

    // The end of the innermost block that always leaves the function (or of the return or throw)
    // around an await: nothing after it is reachable from the await. A block that only breaks out of a
    // loop or switch still reaches the code after it, and a finally block still runs on the way out.
    function reachEnd(node) {
      let end = null;
      for (let child = node, parent = node.parent; parent && parent !== fn.body && parent !== fn;
        child = parent, parent = parent.parent) {
        if (end === null && (ts.isReturnStatement(parent) || ts.isThrowStatement(parent))) end = parent.getEnd();
        else if (end === null && ts.isBlock(parent) && exitKind(parent) === 'function') end = parent.getEnd();
        if (end !== null && ts.isTryStatement(parent) && parent.finallyBlock && child !== parent.finallyBlock) {
          end = parent.getEnd();
        }
      }
      return end === null ? null : pos(end);
    }

    // Where a guard that exits by break or continue stops covering: the end of the statement its jumps
    // leave. Branches that leave different statements take the later end, which lists more windows.
    function guardEnd(ifStatement) {
      const ends = terminalJumps(ifStatement.thenStatement).map(jumpTarget).filter(Boolean).map((t) => t.getEnd());
      return ends.length ? pos(Math.max(...ends)) : null;
    }

    function recordMemberWrite(target, node, ctx) {
      const member = memberPath(target);
      if (member) {
        ir.memberWrites.push({ path: member, span: spanOf(node), line: lineOf(node), inHandler: ctx.inHandler,
          inline: ctx.inline, scopeIds: ctx.scopeIds });
      }
    }

    function namesOf(binding) {
      if (!binding) return [];
      if (ts.isIdentifier(binding)) return [binding.text];
      if (ts.isObjectBindingPattern(binding) || ts.isArrayBindingPattern(binding)) {
        return binding.elements.flatMap((element) => (ts.isOmittedExpression(element) ? [] : namesOf(element.name)));
      }
      return [];
    }

    function isCollection(init) {
      const node = unwrap(init);
      if (!node) return false;
      if (ts.isArrayLiteralExpression(node) || ts.isObjectLiteralExpression(node)) return true;
      return ts.isNewExpression(node) && ts.isIdentifier(node.expression)
        && COLLECTION_CONSTRUCTORS.has(node.expression.text);
    }

    function visit(node, ctx) {
      if (!node) return;
      if (node !== fn && isFunctionLike(node)) {
        if (!isInlineCallback(node)) return;
        const scope = { id: ir.scopes.length, kind: 'callback', callee: dotted(node.parent.expression),
          callId: ts.isCallExpression(node.parent) ? idOf(node.parent) : null, span: spanOf(node), line: lineOf(node) };
        ir.scopes.push(scope);
        visit(node.body, { ...ctx, inline: true, scopeIds: [...ctx.scopeIds, scope.id] });
        return;
      }
      if (ts.isCallExpression(node)) {
        const callee = dotted(node.expression);
        ir.calls.push({
          id: idOf(node), callee, span: spanOf(node), line: lineOf(node), text: textOf(node),
          awaited: Boolean(outer(node) && ts.isAwaitExpression(outer(node))),
          bare: !callee.includes('.'), inHandler: ctx.inHandler, inline: ctx.inline,
          chainedReceiver: isChainedReceiver(node), sql: isChainedReceiver(node) ? null : sqlOf(node),
          scopeIds: ctx.scopeIds,
        });
      } else if (ts.isAwaitExpression(node)) {
        // An await inside an inline callback is a gap only for writes in that same callback.
        ir.awaits.push({ span: spanOf(node), line: lineOf(node), kind: 'await', reachEnd: reachEnd(node), text: textOf(node),
          inline: ctx.inline, scopeIds: ctx.scopeIds });
      } else if (ts.isForOfStatement(node) && node.awaitModifier) {
        ir.awaits.push({ span: spanOf(node.expression), line: lineOf(node), kind: 'for-await', reachEnd: reachEnd(node),
          text: textOf(node), inline: ctx.inline, scopeIds: ctx.scopeIds });
      } else if (ts.isVariableDeclaration(node) && node.initializer) {
        ir.assigns.push({ names: namesOf(node.name), span: spanOf(node), line: lineOf(node), refs: refsOf(node.initializer),
          collection: isCollection(node.initializer), inline: ctx.inline });
      } else if (ts.isBinaryExpression(node) && ASSIGNMENT_OPERATORS.has(node.operatorToken.kind)) {
        const target = unwrap(node.left);
        if (ts.isIdentifier(target) && node.operatorToken.kind === K.EqualsToken) {
          ir.assigns.push({ names: [target.text], span: spanOf(node), line: lineOf(node), refs: refsOf(node.right),
            collection: isCollection(node.right), inline: ctx.inline });
        } else if (ts.isPropertyAccessExpression(target) || ts.isElementAccessExpression(target)) {
          recordMemberWrite(target, node, ctx);
        }
      } else if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node))
        && (node.operator === K.PlusPlusToken || node.operator === K.MinusMinusToken)) {
        const target = unwrap(node.operand);
        if (ts.isPropertyAccessExpression(target) || ts.isElementAccessExpression(target)) recordMemberWrite(target, node, ctx);
      } else if (ts.isIfStatement(node)) {
        const exits = exitKind(node.thenStatement);
        ir.conds.push({ kind: 'if', span: spanOf(node), condSpan: spanOf(node.expression), line: lineOf(node),
          guard: exits !== null, guardEnd: exits === 'loop' ? guardEnd(node) : null, inline: ctx.inline,
          inHandler: ctx.inHandler, refs: refsOf(node.expression), text: textOf(node) });
      } else if (ts.isConditionalExpression(node)) {
        ir.conds.push({ kind: 'ternary', span: spanOf(node), condSpan: spanOf(node.condition), line: lineOf(node),
          guard: false, inline: ctx.inline, inHandler: ctx.inHandler, refs: refsOf(node.condition), text: textOf(node) });
      }

      if (ts.isTryStatement(node)) {
        visit(node.tryBlock, ctx);
        if (node.catchClause) visit(node.catchClause, { ...ctx, inHandler: true });
        // finally runs on the normal path too, so its writes are not failure bookkeeping.
        if (node.finallyBlock) visit(node.finallyBlock, ctx);
        return;
      }
      ts.forEachChild(node, (child) => visit(child, ctx));
    }

    visit(fn.body, { inline: false, inHandler: false, scopeIds: [] });
    return ir;
  }

  const functions = [];
  (function collect(node) {
    if (isFunctionLike(node) && node.body) functions.push(analyzeFunction(node));
    ts.forEachChild(node, collect);
  })(sf);
  return functions;
}

const out = { typescript: { version: ts.version, path: from }, files: [] };
for (const file of input.files) {
  try {
    out.files.push({ path: file, functions: analyzeFile(file, input.root) });
  } catch (error) {
    out.files.push({ path: file, error: String(error && error.message ? error.message : error) });
  }
}
process.stdout.write(JSON.stringify(out));
