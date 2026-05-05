#!/usr/bin/env node
// HERMES Android fix: patch package.json build script to use Node.js API
// instead of esbuild CLI (native binary is broken on Termux/Android).
import { readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkgPath = join(__dirname, 'package.json');
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
const buildCmd = "node -e \"const esbuild=require('esbuild');esbuild.build({entryPoints:['src/entry-exports.ts'],bundle:true,platform:'node',format:'esm',packages:'external',outdir:'dist',outbase:'src'}).catch(e=>{console.error(e.message);process.exit(1)})\"";

if (!pkg.scripts.build.includes('esbuild')) {
    pkg.scripts.build = buildCmd;
    writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
    console.log('[hermes] esbuild build patched for Android');
}
