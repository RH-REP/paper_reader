// Mac から来る zip（無圧縮で格納したもの）を読む。圧縮された項目は扱わない（bundle.py は無圧縮で作る）。
const td = new TextDecoder();

export function unzipStored(buf) {
  const u8 = new Uint8Array(buf);
  const dv = new DataView(buf);
  // 末尾の「中央ディレクトリの終わり」を探す
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("zip ではありません");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const files = new Map();
  for (let n = 0; n < count; n++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("zip の目次が壊れています");
    const method = dv.getUint16(p + 10, true);
    const size = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true), extraLen = dv.getUint16(p + 30, true), commentLen = dv.getUint16(p + 32, true);
    const local = dv.getUint32(p + 42, true);
    const name = td.decode(u8.subarray(p + 46, p + 46 + nameLen));
    if (method !== 0) throw new Error(`圧縮された項目は読めません: ${name}`);
    const lNameLen = dv.getUint16(local + 26, true), lExtraLen = dv.getUint16(local + 28, true);
    const start = local + 30 + lNameLen + lExtraLen;
    files.set(name, u8.subarray(start, start + size));
    p += 46 + nameLen + extraLen + commentLen;
  }
  return files;
}

export const text = (bytes) => td.decode(bytes);
