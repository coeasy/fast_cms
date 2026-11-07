// @ts-check
/* ==========================================================================
   core/time.js —— 系统时区与时间格式化
   统一用系统时区展示墙上时间，UTC 与墙上时间互转供日期筛选使用。
   ========================================================================== */

let systemTimezone;

export function setSystemTimezone(name) {
  if (!name) return;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name });
    systemTimezone = name;
  } catch {
    systemTimezone = undefined;
  }
}

function parts(date, options) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: systemTimezone,
    hour12: false,
    ...options,
  });
  const out = {};
  formatter.formatToParts(date).forEach((part) => {
    if (part.type !== 'literal') out[part.type] = part.value;
  });
  return out;
}

const pad = (value) => String(value).padStart(2, '0');

export function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const p = parts(date, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  return `${p.year}-${p.month}-${p.day} ${pad(Number(p.hour) % 24)}:${p.minute}:${p.second}`;
}

export function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const p = parts(date, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}-${p.month}-${p.day}`;
}

export function formatRecentTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const p = parts(date, {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
  return `${p.month}-${p.day} ${pad(Number(p.hour) % 24)}:${p.minute}`;
}

/** 墙上时间（datetime-local 的值）→ 系统时区对应的 UTC ISO */
export function wallToUtcIso(local) {
  if (!local) return null;
  const [datePart, timePart = '00:00'] = String(local).split('T');
  const [y, m, d] = datePart.split('-').map(Number);
  const [hh, mm, ss = 0] = timePart.split(':').map(Number);
  if (!y || !m || !d) return null;
  const guess = Date.UTC(y, m - 1, d, hh || 0, mm || 0, ss);
  const zone = systemTimezone;
  if (!zone) return new Date(guess).toISOString();
  const p = parts(new Date(guess), {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  const asUtc = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour) % 24, Number(p.minute), Number(p.second));
  return new Date(guess - (asUtc - guess)).toISOString();
}

/** UTC ISO → 系统时区的 datetime-local 值 */
export function utcIsoToWall(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const p = parts(date, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  return `${p.year}-${p.month}-${p.day}T${pad(Number(p.hour) % 24)}:${p.minute}`;
}

/** 日期筛选按展示时区取整天边界，否则跨时区会漏掉首尾两天的记录 */
export function startOfDayIso(dateStr) {
  return dateStr ? wallToUtcIso(`${dateStr}T00:00:00`) : undefined;
}

export function endOfDayIso(dateStr) {
  return dateStr ? wallToUtcIso(`${dateStr}T23:59:59`) : undefined;
}
