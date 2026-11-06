import test from 'node:test'
import assert from 'node:assert/strict'
import { parseMarkdownMath, renderMathFormula } from './markdownMath.mjs'
import { markdownParagraphs } from './artifacts.mjs'

test('all four delimiters preserve TeX before Markdown escaping', () => {
  const result = parseMarkdownMath(String.raw`电压 \(U=IR\)，功率 $P=UI$。

\[ I=\frac{U}{R} \]

$$ P=I^2R $$`)
  assert.deepEqual(result.formulas.map(item => item.display), [false, false, true, true])
  assert.equal(result.formulas[0].text, 'U=IR')
  assert.match(result.formulas[2].text, /\\frac/)
  for (const formula of result.formulas) assert.match(renderMathFormula(formula.text, formula.display), /class="katex"/)
})

test('code, escaped dollars, normal amounts, and raw HTML attributes remain literal', () => {
  const input = '费用 $5 和 $10，预算 $100。\\$x$。`$x$`\n\n```tex\n\\(U=IR\\)\n$$x$$\n```\n\n<a title="$x$">链接</a><code>$x$</code><pre>\\(x\\)</pre>'
  const result = parseMarkdownMath(input)
  assert.equal(result.formulas.length, 0, JSON.stringify(result.formulas))
  assert.match(result.html, /<code>\$x\$<\/code>/)
  assert.match(result.html, /费用 \$5 和 \$10/)
})

test('math rejects executable URLs and handles invalid TeX without breaking the document', () => {
  assert.doesNotMatch(renderMathFormula(String.raw`\href{javascript:alert(1)}{click}`), /href=/)
  assert.doesNotMatch(renderMathFormula(String.raw`\htmlStyle{color:red}{x}`), /style="color:red/)
  assert.match(renderMathFormula(String.raw`\frac{`), /katex-error/)
})

test('rendering leaves exact Unicode paragraph selections and source untouched', () => {
  const content = '😀 电压 \\(U=IR\\)，不要改变原文。\r\n\r\n功率 $P=UI$。'
  const resource = { id: 1, type: 'lecture', version: 1, content }
  const before = markdownParagraphs(resource)
  parseMarkdownMath(content)
  assert.deepEqual(markdownParagraphs(resource), before)
  for (const item of before.paragraphs) assert.equal(Array.from(content).slice(item.start, item.end).join(''), item.text)
})

test('physics formulas render inside tables, lists and multiline display blocks', () => {
  const parsed = parseMarkdownMath(String.raw`| 量 | 关系 |
|---|---|
| 电流 | \(I=\frac{U}{R}\) |

- 电功率 \(P=I^2R\)

\[
\begin{aligned}
U &= IR \\
P &= \frac{U^2}{R}
\end{aligned}
\]`)
  assert.equal(parsed.formulas.length, 3)
  for (const formula of parsed.formulas) assert.doesNotMatch(renderMathFormula(formula.text, formula.display), /katex-error/)
})
