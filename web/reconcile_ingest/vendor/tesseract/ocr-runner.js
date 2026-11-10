/** App-owned supervisor; keeps cancellation responsive during nested WASM work. */
import Tesseract from './tesseract.esm.min.js';
const { createWorker } = Tesseract;

const nativeWorker = self.Worker;
const children = new Set();
// Tesseract does not return its worker until language initialization is finished.
// Track workers inside this isolated supervisor so init errors/timeouts can free
// them too. This never changes the application's window.Worker constructor.
self.Worker = class extends nativeWorker {
  constructor(...args) {
    super(...args);
    children.add(this);
  }
  terminate() {
    children.delete(this);
    return super.terminate();
  }
};

let active = false;
let finished = false;
let lastProgress = 0;

function releaseChildren() {
  for (const child of children) child.terminate();
  children.clear();
}

function finish(message) {
  if (finished) return;
  finished = true;
  releaseChildren();
  if (message) self.postMessage(message);
  self.close();
}

function report({ status, progress = 0 }) {
  const phases = {
    'loading tesseract core': ['正在加载本地识别引擎', 0.02, 0.10],
    'initializing tesseract': ['正在初始化识别引擎', 0.12, 0.03],
    'loading language traineddata': ['正在加载本地中文和英文模型', 0.15, 0.20],
    'initializing api': ['正在准备文字识别', 0.35, 0.10],
    'recognizing text': ['正在识别截图文字', 0.45, 0.54],
  };
  const [label, offset, weight] = phases[status] || ['正在准备截图识别', 0, 0];
  lastProgress = Math.max(lastProgress, offset + Math.max(0, Math.min(1, progress)) * weight);
  if (!finished) self.postMessage({ type: 'progress', status: label, progress: lastProgress });
}

self.onmessage = async ({ data }) => {
  if (data?.type === 'abort') return finish(null);
  if (data?.type !== 'recognize' || active) return;
  active = true;
  let engine;
  try {
    engine = await createWorker(['chi_sim', 'eng'], 1, {
      workerPath: new URL('./worker.min.js', import.meta.url).href,
      corePath: new URL('./core/tesseract-core-lstm.wasm.js', import.meta.url).href,
      langPath: new URL('./lang', import.meta.url).href,
      workerBlobURL: false,
      gzip: false,
      cacheMethod: 'none',
      legacyCore: false,
      legacyLang: false,
      logger: report,
      errorHandler: () => finish({ type: 'error' }),
    });
    if (finished) { await engine.terminate(); return; }
    await engine.setParameters({
      tessedit_pageseg_mode: '6',
      preserve_interword_spaces: '1',
      user_defined_dpi: '300',
    });
    const { data: result } = await engine.recognize(data.bytes, {}, { text: true });
    await engine.terminate();
    finish({ type: 'result', text: result.text, confidence: result.confidence });
  } catch {
    if (engine) await engine.terminate().catch(() => {});
    finish({ type: 'error' });
  }
};
