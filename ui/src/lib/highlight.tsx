import type { ReactNode } from "react";

const RULES: [string, RegExp][] = [
  ["comment", /^(\/\/|#)[^\n]*/],
  ["string", /^(?:f?("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|`(?:[^`\\]|\\.)*`)/],
  ["keyword", /^(?:import|from|as|return|async|await|yield|raise|try|except|finally|if|elif|else|for|while|with|break|continue|catch|throw)\b/],
  ["definition", /^(?:def|class|lambda|function|const|let|var)\b/],
  ["constant", /^(?:None|True|False|true|false|null|undefined|self|this)\b/],
  ["type", /^(?:str|int|float|bool|list|dict|set|tuple|Dict|Any|Optional|List|Union|Callable|Promise|Array|Record|string|number|boolean|void)\b/],
  ["number", /^\b\d+(?:\.\d+)?\b/],
  ["function", /^[a-zA-Z_]\w*(?=\s*\()/],
  ["operator", /^(?:=>|->|===|!==|==|!=|<=|>=|[=+\-*/%&|^!<>])/],
  ["ident", /^[a-zA-Z_]\w*/],
  ["space", /^\s+/],
];

const STYLES: Record<string, React.CSSProperties> = {
  comment: { color: "#6a9955", fontStyle: "italic" },
  string: { color: "#ce9178" },
  keyword: { color: "#c586c0", fontWeight: 600 },
  definition: { color: "#569cd6", fontWeight: 600 },
  constant: { color: "#569cd6" },
  type: { color: "#4ec9b0" },
  function: { color: "#dcdcaa" },
  number: { color: "#b5cea8" },
  operator: { color: "#d4d4d8" },
  ident: { color: "#9cdcfe" },
};

export function highlight(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = code;
  while (rest) {
    const rule = RULES.find(([, rx]) => rx.test(rest));
    const value = rule ? (rest.match(rule[1]) as RegExpMatchArray)[0] : rest[0];
    const style = rule && STYLES[rule[0]];
    out.push(style ? (
      <span key={out.length} style={style}>
        {value}
      </span>
    ) : (
      value
    ));
    rest = rest.slice(value.length);
  }
  return out;
}
