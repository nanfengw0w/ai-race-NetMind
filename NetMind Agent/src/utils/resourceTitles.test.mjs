import test from 'node:test'
import assert from 'node:assert/strict'
import { resourceTitleTopic } from './resourceTitles.mjs'

test('resource title prefixes are removed without shortening semantic text', () => {
  const math = '二次函数顶点式 y=a(x-h)²+k：平移与伸缩'
  for (const prefix of ['PPT课件 - ', 'PPT课件 - PPT课件 - ', '课程讲解 - ', '多形式讲解：']) {
    assert.equal(resourceTitleTopic(prefix + math), math)
  }
  assert.equal(resourceTitleTopic('高中物理：理想直流电路的串联和并联电阻、电压与电流关系'), '高中物理：理想直流电路的串联和并联电阻、电压与电流关系')
  assert.equal(resourceTitleTopic(''), '')
})
