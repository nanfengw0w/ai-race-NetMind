// Stored resource titles already describe the generated work. Do not run them
// through query/topic shortening: punctuation and formulas are meaningful.
export function resourceTitleTopic(value) {
  let title = String(value || '').trim()
  const prefix = /^(?:课程讲解|多形式讲解|文字讲解|HTML\s*课件|PPT\s*课件|互动网页|互动教学|交互教学|练习题|思维导图|实操案例|学习路径|评估报告)\s*[-—:：]\s*/i
  while (prefix.test(title)) title = title.replace(prefix, '').trim()
  return title
}
