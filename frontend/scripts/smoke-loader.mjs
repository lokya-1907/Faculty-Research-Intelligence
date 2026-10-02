// Node loader hook: transpile .jsx with esbuild (already a Vite dependency) so
// the smoke render can import the app source directly, without a browser.
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

let esbuild = null;
async function getEsbuild() {
  if (!esbuild) esbuild = await import('esbuild');
  return esbuild;
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('.jsx') || url.endsWith('.css')) {
    if (url.endsWith('.css')) {
      return { format: 'module', shortCircuit: true, source: 'export default {};' };
    }
    const filename = fileURLToPath(url);
    const source = await readFile(filename, 'utf8');
    const { transform } = await getEsbuild();
    const result = await transform(source, {
      loader: 'jsx',
      format: 'esm',
      jsx: 'automatic',
      jsxImportSource: 'react',
      sourcefile: filename,
    });
    return { format: 'module', shortCircuit: true, source: result.code };
  }
  return nextLoad(url, context);
}
