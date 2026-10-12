export async function api(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(`/api${path}`, {
    method,
    signal,
    cache: "no-store",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("服务返回了无效响应，请检查连接与日志");
  }
  if (!response.ok || data.ok === false) {
    const detail = data.error || data.detail || data.message || data.errors;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((x) => x.msg || x).join("；")
          : `请求失败（${response.status}）`,
    );
  }
  return data;
}
export function paymentPayload(payment, ok) {
  const id = String(payment?.order_id || "").trim();
  if (!id) throw new Error("未取得待确认订单号，请刷新后重新核对");
  return { ok, expected_order_id: id };
}
export function transactionIdentity(row) {
  if (!Number.isInteger(row.idx) || row.idx < 0)
    throw new Error("交易记录标识无效，请刷新后重试");
  return {
    type: row.type || "purchase",
    idx: row.idx,
    ...(row.db_id ? { db_id: row.db_id } : {}),
  };
}
export const clone = (value) => JSON.parse(JSON.stringify(value));
export const amount = (value) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toFixed(2);
export const dateTime = (value) =>
  value
    ? new Date(typeof value === "number" ? value * 1000 : value).toLocaleString(
        "zh-CN",
      )
    : "—";
export function download(name, value, type = "application/json") {
  const url = URL.createObjectURL(
    new Blob(
      [typeof value === "string" ? value : JSON.stringify(value, null, 2)],
      { type },
    ),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name.replace(/[\\/:*?"<>|]/g, "_");
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
