#!/usr/bin/env node
/**
 * Zero-build frontend JS syntax & TypeScript `checkJs` verification gate.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FRONTEND_DIR = path.join(REPO_ROOT, 'frontend');
const JS_DIR = path.join(FRONTEND_DIR, 'assets', 'js');

function listJsFiles(dir) {
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith('.js'))
    .sort()
    .map((name) => path.join(dir, name));
}

function checkSyntax(files) {
  for (const file of files) {
    execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
  }
}

function resolveTypeScript() {
  const candidates = [
    process.env.TYPESCRIPT_JS,
    path.join(REPO_ROOT, 'node_modules', 'typescript', 'lib', 'typescript.js'),
    '/Applications/Visual Studio Code.app/Contents/Resources/app/extensions/node_modules/typescript/lib/typescript.js',
    '/Applications/Cursor.app/Contents/Resources/app/extensions/node_modules/typescript/lib/typescript.js',
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return { path: candidate, ts: require(candidate) };
    }
  }
  try {
    const resolved = require.resolve('typescript');
    return { path: resolved, ts: require(resolved) };
  } catch {
    return null;
  }
}

function checkTypes(tsInfo) {
  const { ts } = tsInfo;
  const configPath = path.join(FRONTEND_DIR, 'jsconfig.json');
  const readRes = ts.readConfigFile(configPath, ts.sys.readFile);
  if (readRes.error) {
    console.error(ts.flattenDiagnosticMessageText(readRes.error.messageText, '\n'));
    return 1;
  }
  const parsed = ts.parseJsonConfigFileContent(readRes.config, ts.sys, FRONTEND_DIR);
  const program = ts.createProgram(parsed.fileNames, parsed.options);
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length > 0) {
    for (const d of diagnostics) {
      const msg = ts.flattenDiagnosticMessageText(d.messageText, ' ');
      if (d.file && typeof d.start === 'number') {
        const { line, character } = d.file.getLineAndCharacterOfPosition(d.start);
        const rel = path.relative(REPO_ROOT, d.file.fileName);
        console.error(`${rel}:${line + 1}:${character + 1} TS${d.code}: ${msg}`);
      } else {
        console.error(`TS${d.code}: ${msg}`);
      }
    }
    return diagnostics.length;
  }
  return 0;
}

const files = listJsFiles(JS_DIR);
checkSyntax(files);
console.log(`Syntax check passed (${files.length} JS files).`);

const requireTs = process.argv.includes('--require-ts');
const tsInfo = resolveTypeScript();
if (!tsInfo) {
  const note = 'TypeScript compiler library not found; skipped checkJs pass.';
  if (requireTs) {
    console.error(note);
    process.exit(1);
  }
  console.log(note);
  process.exit(0);
}

const errCount = checkTypes(tsInfo);
if (errCount > 0) {
  console.error(`checkJs failed with ${errCount} diagnostic(s).`);
  process.exit(1);
}
console.log(`checkJs passed (0 diagnostics, compiler: ${tsInfo.path}).`);
