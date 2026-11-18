import { useEffect, useRef, useState } from 'react';

// UI-only data, scoped to this browser/Electron origin. Not part of backend backups.
const DATABASE = 'loqman-profile-ui-v1';
const STORE = 'photos';
const KEY = 'user';
const MAX_BYTES = 10 * 1024 * 1024;

function photoStorage(action: 'read' | 'write' | 'remove', photo?: Blob): Promise<Blob | null> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    let blocked = false;
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onerror = () => reject(new Error('Photo storage is unavailable. Please try again.'));
    request.onblocked = () => {
      blocked = true;
      reject(new Error('Close other Loqman windows and try again.'));
    };
    request.onsuccess = () => {
      const db = request.result;
      if (blocked) { db.close(); return; }
      db.onversionchange = () => db.close();
      try {
        const transaction = db.transaction(STORE, action === 'read' ? 'readonly' : 'readwrite');
        const store = transaction.objectStore(STORE);
        const operation = action === 'read' ? store.get(KEY) : action === 'remove' ? store.delete(KEY) : store.put(photo, KEY);
        transaction.oncomplete = () => {
          db.close();
          resolve(action === 'read' && operation.result instanceof Blob ? operation.result : null);
        };
        transaction.onabort = transaction.onerror = () => {
          db.close();
          reject(new Error('Could not save the photo. Device storage may be unavailable or full.'));
        };
      } catch {
        db.close();
        reject(new Error('Photo storage is unavailable. Please try again.'));
      }
    };
  });
}

async function processPhoto(file: File): Promise<Blob> {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    throw new Error('Choose a PNG, JPEG or WebP image.');
  }
  if (file.size > MAX_BYTES) throw new Error('Choose an image up to 10 MB.');
  const header = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  const startsWith = (bytes: number[], offset = 0) => bytes.every((byte, i) => header[offset + i] === byte);
  const validType = file.type === 'image/png'
    ? startsWith([137, 80, 78, 71, 13, 10, 26, 10])
    : file.type === 'image/jpeg'
      ? startsWith([255, 216, 255])
      : startsWith([82, 73, 70, 70]) && startsWith([87, 69, 66, 80], 8);
  if (!validType) throw new Error('This image could not be read. Choose another PNG, JPEG or WebP.');
  let image: ImageBitmap;
  try { image = await createImageBitmap(file); }
  catch { throw new Error('This image could not be read. Choose another PNG, JPEG or WebP.'); }
  try {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 256;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Image processing is unavailable. Please try again.');
    const side = Math.min(image.width, image.height);
    context.drawImage(image, (image.width - side) / 2, (image.height - side) / 2, side, side, 0, 0, 256, 256);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      blob => blob ? resolve(blob) : reject(new Error('Could not process this image. Please try another.')),
      'image/webp', 0.9,
    ));
  } finally { image.close(); }
}

export function useProfilePhoto() {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const currentUrl = useRef<string | null>(null);
  const changing = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    let alive = true;
    mounted.current = true;
    photoStorage('read').then(photo => {
      if (!alive || !photo) return;
      currentUrl.current = URL.createObjectURL(photo);
      setUrl(currentUrl.current);
    }).catch(() => {
      if (alive) setError('Could not restore your photo. Photo storage may be unavailable.');
    }).finally(() => { if (alive) setBusy(false); });
    return () => {
      alive = false;
      mounted.current = false;
      if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
      currentUrl.current = null;
    };
  }, []);

  const change = async (file: File | null) => {
    if (busy || changing.current) return;
    changing.current = true;
    setBusy(true);
    setError(null);
    try {
      const photo = file ? await processPhoto(file) : null;
      await photoStorage(photo ? 'write' : 'remove', photo ?? undefined);
      if (!mounted.current) return;
      const nextUrl = photo ? URL.createObjectURL(photo) : null;
      if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
      currentUrl.current = nextUrl;
      setUrl(nextUrl);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : 'Could not update your photo. Please try again.');
    } finally {
      changing.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  return { url, busy, error, change };
}

export type ProfilePhoto = ReturnType<typeof useProfilePhoto>;
