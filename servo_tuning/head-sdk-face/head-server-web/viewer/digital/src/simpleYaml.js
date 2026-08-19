function parseScalar(rawValue) {
  const value = rawValue.trim();

  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith('\'') && value.endsWith('\''))) {
    return value.slice(1, -1);
  }

  if (value === 'true') return true;
  if (value === 'false') return false;
  if (value === 'null') return null;
  if (/^-?\d+(?:\.\d+)?$/.test(value)) return Number(value);

  return value;
}

export function parseSimpleYaml(text = '') {
  const root = {};
  const stack = [{ indent: -1, value: root }];
  const lines = text.replace(/\r\n?/g, '\n').split('\n');

  for (const rawLine of lines) {
    if (!rawLine.trim() || rawLine.trim().startsWith('#')) continue;
    if (rawLine.includes('\t')) {
      throw new Error('Tabs are not supported in remote config YAML');
    }

    const indent = rawLine.match(/^ */)[0].length;
    const trimmed = rawLine.trim();
    const separatorIndex = trimmed.indexOf(':');

    if (separatorIndex <= 0) {
      throw new Error(`Invalid YAML line: ${trimmed}`);
    }

    const key = trimmed.slice(0, separatorIndex).trim();
    const rawValue = trimmed.slice(separatorIndex + 1);

    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }

    const parent = stack[stack.length - 1]?.value;
    if (!parent || typeof parent !== 'object' || Array.isArray(parent)) {
      throw new Error(`Invalid YAML nesting near: ${trimmed}`);
    }

    if (!rawValue.trim()) {
      const child = {};
      parent[key] = child;
      stack.push({ indent, value: child });
      continue;
    }

    parent[key] = parseScalar(rawValue);
  }

  return root;
}
