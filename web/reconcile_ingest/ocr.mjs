/** Local-only OCR. Recognition extracts text; callers must keep all facts pending. */
export const OCR_ASSETS = Object.freeze({
  runnerPath: new URL('./vendor/tesseract/ocr-runner.js', import.meta.url).href,
  workerPath: new URL('./vendor/tesseract/worker.min.js', import.meta.url).href,
  corePath: new URL('./vendor/tesseract/core/tesseract-core-lstm.wasm.js', import.meta.url).href,
  langPath: new URL('./vendor/tesseract/lang', import.meta.url).href,
  languages: ['chi_sim', 'eng'],
});

const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const MESSAGES = {
  OCR_TIMEOUT: '截图识别超时，已停止任务。请裁剪到需要的文字区域后重试，或直接粘贴文字。',
  OCR_ABORTED: '截图识别已取消。',
  OCR_EMPTY: '没有识别到文字。请使用清晰原图，裁剪到文字区域后重试，或手工粘贴文字。',
  OCR_UNSUPPORTED: '请导入 PNG、JPG、WebP、BMP 或 GIF 截图（最大 20 MB）。',
  OCR_ENGINE: '截图识别失败。请确认本地服务已启动且 OCR 资源完整，使用清晰的 PNG/JPG 重试；也可直接粘贴文字。',
};

function ocrError(code) {
  const error = new Error(MESSAGES[code] || MESSAGES.OCR_ENGINE);
  error.name = 'OCRError';
  error.code = code;
  return error;
}

async function imageBytes(input) {
  if (input instanceof Blob) {
    const typeOkay = /^image\/(png|jpe?g|webp|bmp|gif)$/i.test(input.type);
    const extensionOkay = !input.type && /\.(png|jpe?g|webp|bmp|gif)$/i.test(input.name || '');
    if ((!typeOkay && !extensionOkay) || !input.size || input.size > MAX_IMAGE_BYTES) {
      throw ocrError('OCR_UNSUPPORTED');
    }
    return new Uint8Array(await input.arrayBuffer());
  }
  // Do not allow URLs here: screenshots and image content must never be fetched remotely.
  if (typeof input !== 'string' || !/^data:image\/(png|jpe?g|webp|bmp|gif);base64,/i.test(input)) {
    throw ocrError('OCR_UNSUPPORTED');
  }
  const base64 = input.slice(input.indexOf(',') + 1);
  if (!base64.length || base64.length > Math.ceil(MAX_IMAGE_BYTES / 3) * 4) {
    throw ocrError('OCR_UNSUPPORTED');
  }
  try {
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    if (!bytes.length || bytes.length > MAX_IMAGE_BYTES) throw ocrError('OCR_UNSUPPORTED');
    return bytes;
  } catch {
    throw ocrError('OCR_UNSUPPORTED');
  }
}

/**
 * @param {File|Blob|string} fileOrDataURL A selected raster image or raster base64 data URL.
 * @param {{onProgress?:function, timeoutMs?:number, signal?:AbortSignal}} options
 * onProgress receives { status: Chinese label, progress: 0..1 }.
 * confidence is an OCR engine score (0..100), not business-fact confidence.
 */
export async function recognizeImage(fileOrDataURL, { onProgress, timeoutMs = 120000, signal } = {}) {
  if (signal?.aborted) throw ocrError('OCR_ABORTED');
  if (typeof Worker !== 'function' || typeof WebAssembly !== 'object') throw ocrError('OCR_ENGINE');
  let bytes;
  try {
    bytes = await imageBytes(fileOrDataURL);
  } catch (error) {
    throw error?.name === 'OCRError' ? error : ocrError('OCR_ENGINE');
  }
  if (signal?.aborted) throw ocrError('OCR_ABORTED');
  const deadline = Number.isFinite(timeoutMs) && timeoutMs > 0 ? Math.min(timeoutMs, 300000) : 120000;
  return new Promise((resolve, reject) => {
    let worker;
    let settled = false;
    let timer;
    const progress = (value) => {
      try { onProgress?.(value); } catch { /* UI callbacks cannot strand OCR workers. */ }
    };
    const cleanup = (allowCooperativeStop = false) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      if (!worker) return;
      worker.onmessage = null;
      worker.onerror = null;
      worker.onmessageerror = null;
      if (allowCooperativeStop) {
        // The supervisor stays responsive while the nested WASM worker is busy.
        // Stop that worker first, then unconditionally release the supervisor too.
        try { worker.postMessage({ type: 'abort' }); } catch { /* Already stopped. */ }
        setTimeout(() => worker.terminate(), 100);
      } else {
        worker.terminate();
      }
    };
    const fail = (code, cooperative = false) => {
      if (settled) return;
      settled = true;
      cleanup(cooperative);
      reject(ocrError(code));
    };
    const abort = () => fail('OCR_ABORTED', true);
    try {
      worker = new Worker(OCR_ASSETS.runnerPath, { type: 'module', name: 'sop-local-ocr' });
      worker.onmessage = ({ data }) => {
        if (settled) return;
        if (data?.type === 'progress') {
          progress({ status: data.status, progress: data.progress });
        } else if (data?.type === 'result') {
          const text = String(data.text || '').replace(/\r\n?/g, '\n').trim();
          if (!text) return fail('OCR_EMPTY');
          settled = true;
          cleanup();
          progress({ status: '文字识别完成，请核对原图', progress: 1 });
          const confidence = Number(data.confidence);
          resolve({ text, confidence: Number.isFinite(confidence) ? Math.max(0, Math.min(100, confidence)) : 0 });
        } else if (data?.type === 'error') {
          fail('OCR_ENGINE');
        }
      };
      worker.onerror = (event) => { event.preventDefault(); fail('OCR_ENGINE', true); };
      worker.onmessageerror = () => fail('OCR_ENGINE', true);
      timer = setTimeout(() => fail('OCR_TIMEOUT', true), deadline);
      signal?.addEventListener('abort', abort, { once: true });
      progress({ status: '正在加载本地中文识别资源', progress: 0 });
      worker.postMessage({ type: 'recognize', bytes }, [bytes.buffer]);
    } catch {
      fail('OCR_ENGINE', true);
    }
  });
}
