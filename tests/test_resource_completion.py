import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from services import chat_service


class ResourceCompletionTests(unittest.TestCase):
    def test_failed_or_partial_generation_is_not_reported_as_all_done(self):
        self.assertEqual(chat_service._resource_completion_summary(0, 1),
                         '本次未能生成可用资源，请重新发送问题重试。')
        partial = chat_service._resource_completion_summary(1, 3)
        self.assertIn('已生成 1 个', partial)
        self.assertIn('2 个未完成', partial)
        self.assertNotIn('已完成', partial)
        self.assertEqual(chat_service._resource_completion_summary(2, 2),
                         '已完成 2 个学习资源，可在右侧查看。')

if __name__ == '__main__':
    unittest.main()
