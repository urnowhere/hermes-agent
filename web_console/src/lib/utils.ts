export function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ');
}

export function titleCase(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
