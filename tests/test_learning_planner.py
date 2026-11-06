import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from agents.tutor_agent import TutorPlan, _explicit_product_tools, _respect_requested_products, plan_tutor_actions, reflect_resource
from agents.topic_utils import compact_resource_title, lecture_title_topic


class LearningPlannerTests(unittest.TestCase):
    def test_subject_and_explanatory_words_do_not_request_other_products(self):
        for request in (
            '请制作可播放的PPT课件，完整讲授以下主题：高中数学：二次函数顶点式 y=a(x-h)^2+k（a≠0），理解 a、h、k 的作用、顶点、对称轴、开口和伸缩，区分平移与形状变化，用实例说明。',
            '制作PPT，围绕这个问题帮助理解二次函数',
            '制作PPT，说明物理适用条件、操作过程和案例，巩固理解并检验结论，介绍实验报告和路径规划的原理',
        ):
            with self.subTest(request=request):
                tools = _explicit_product_tools(request)
                self.assertFalse(set(tools) & {'quiz', 'lab', 'path', 'assessment'})

    def test_explicit_quizzes_and_multiple_products_remain_supported(self):
        for request in ('请出题', '给我三道题', '制作PPT并出两道计算题', '制作PPT和练习题'):
            with self.subTest(request=request):
                self.assertIn('quiz', _explicit_product_tools(request))
        tools = _explicit_product_tools('制作PPT，并生成实验任务书、学习计划和评估报告')
        self.assertTrue({'ppt', 'lab', 'path', 'assessment'} <= set(tools))

    @patch('agents.tutor_agent.get_active_memory_text', return_value='')
    @patch('agents.tutor_agent.get_learning_signal_summary', return_value={})
    @patch('agents.tutor_agent._recent_records', return_value=[])
    @patch('agents.tutor_agent.chat', side_effect=AssertionError('must not call model'))
    def test_selected_ppt_with_subject_only_does_not_add_quiz(self, *_):
        result = plan_tutor_actions('test', '请制作PPT，完整讲授以下主题：二次函数，理解顶点问题', {}, required_tools=['ppt'])
        self.assertEqual(result.intents, ['ppt'])

    def test_selected_products_own_their_explanations_and_embedded_exercises(self):
        for selected in ('ppt', 'lecture', 'lab', 'mindmap', 'quiz', 'path', 'assessment'):
            plan = TutorPlan('理解', [], '', [{'tool': tool} for tool in dict.fromkeys([selected, 'lecture'])])
            result = _respect_requested_products(plan, '解释改变电阻时哪些量变化，用案例说明', [selected])
            self.assertEqual(result.intents, [selected])
        plan = TutorPlan('理解', [], '', [{'tool': 'ppt'}, {'tool': 'lecture'}, {'tool': 'quiz'}])
        self.assertEqual(_respect_requested_products(plan, 'PPT中解释电阻变化，课件内包含练习题', ['ppt']).intents, ['ppt'])
        self.assertEqual(_respect_requested_products(plan, '制作PPT，另外生成文字讲解和练习题', ['ppt']).intents, ['ppt', 'lecture', 'quiz'])
        self.assertEqual(_respect_requested_products(plan, '解释电阻变化', ['ppt', 'lecture']).intents, ['ppt', 'lecture'])
        self.assertEqual(_respect_requested_products(plan, '制作PPT并出两道计算题', ['ppt']).intents, ['ppt', 'quiz'])

    def test_complete_generated_formula_titles_are_preserved(self):
        for kind, prefix in (('ppt', 'PPT课件'), ('lecture', '课程讲解')):
            title = '二次函数顶点式 y=a(x-h)²+k'
            self.assertEqual(compact_resource_title(title, kind), f'{prefix} - {title}')
            self.assertEqual(compact_resource_title(f'{prefix} - {title}', kind), f'{prefix} - {title}')
            self.assertEqual(compact_resource_title(f'{prefix} - {prefix} - {title}', kind), f'{prefix} - {title}')
        self.assertEqual(compact_resource_title('高中数学：二次函数顶点式 y=a(x-h)^2+k', 'ppt'), 'PPT课件 - 高中数学：二次函数顶点式 y=a(x-h)^2+k')

    def test_lecture_titles_ignore_salutations_and_section_openers(self):
        query = '请生成文字讲解及适合的结构化图解：高中物理：理想直流电路中的串联和并联电阻、电压与电流关系。明确适用条件。'
        content = '小A，下面把电路讲清楚。\n\n## 1. 先明确适用条件'
        title = '理想直流电路的串联与并联：总量、支路量与电源条件'
        self.assertEqual(lecture_title_topic(content, {'title': title}, query), title)
        self.assertEqual(lecture_title_topic(content, None, query), '理想直流电路中的串联和并联电阻、电压与电流关系')
        self.assertEqual(lecture_title_topic('# 二次函数顶点式 y=a(x-h)²+k\n\n正文', None, query), '二次函数顶点式 y=a(x-h)²+k')
        self.assertEqual(lecture_title_topic(content, {'title': '小A'}, '小A，下面开始', {'当前课程': '高中物理'}), '高中物理')

    def test_interactive_teaching_and_animation_names_select_interactive_resource(self):
        for request in ('生成单摆的互动教学', '制作水循环交互动画', '用交互教学讲解欧姆定律'):
            with self.subTest(request=request):
                self.assertEqual(_explicit_product_tools(request), ['interactive'])

    def test_short_resources_have_no_implicit_length_or_page_quotas(self):
        resources = [
            {'type': 'lecture', 'content': '这是一段简短的概念说明。'},
            {'type': 'lab', 'content': '改变输入参数，观察对应结果。'},
            {'type': 'assessment', 'content': '目前资料不足以评价掌握情况。'},
            {'type': 'ppt', 'content': '<html><body><section class="slide">单页课件</section></body></html>'},
            {'type': 'lecture', 'content': 'Python里的...可以是合法表达，不表示截断。'},
        ]
        for resource in resources:
            with self.subTest(resource['type']):
                self.assertTrue(reflect_resource(resource, {'tool': resource['type']})['passed'])

    def test_quiz_has_no_default_count_but_respects_explicit_count(self):
        resource = {'type': 'quiz', 'content': '[]', 'metadata': {'questions': [{'question': '判断此句', 'answer': False}]}}
        self.assertTrue(reflect_resource(resource, {'tool': 'quiz', 'instruction': '给我练习'})['passed'])
        self.assertFalse(reflect_resource(resource, {'tool': 'quiz', 'instruction': '给我2道练习'})['passed'])

    def test_resource_review_still_rejects_missing_required_content_or_format(self):
        resources = [
            {'type': 'lecture', 'content': ''},
            {'type': 'quiz', 'content': 'not json'},
            {'type': 'quiz', 'content': '[]', 'metadata': {'questions': [{'question': '题干'}]}},
            {'type': 'mindmap', 'content': 'plain text'},
            {'type': 'ppt', 'content': '<html><body>unfinished'},
        ]
        for resource in resources:
            with self.subTest(resource):
                self.assertFalse(reflect_resource(resource, {'tool': resource['type']})['passed'])

    def test_no_automatic_quizzes_or_reports_from_model(self):
        plan = TutorPlan("理解", [], "推荐", [
            {"tool": "quiz"}, {"tool": "assessment"}, {"tool": "path"},
        ])
        result = _respect_requested_products(plan, "什么是光合作用？", [])
        self.assertEqual(result.intents, ["lecture"])

    def test_optional_exercise_still_works_when_requested(self):
        plan = TutorPlan("理解", [], "推荐", [{"tool": "quiz"}, {"tool": "lecture"}])
        self.assertEqual(_respect_requested_products(plan, "给我两道练习题", []).intents, ["quiz", "lecture"])

    def test_explicit_opt_out_is_not_a_request(self):
        plan = TutorPlan("理解", [], "推荐", [{"tool": "quiz"}, {"tool": "lecture"}])
        self.assertEqual(_respect_requested_products(plan, "讲解即可，不用出题", []).intents, ["lecture"])

    def test_required_buttons_are_explicit(self):
        plan = TutorPlan("理解", [], "推荐", [{"tool": "quiz"}])
        self.assertEqual(_respect_requested_products(plan, "光合作用", ["quiz"]).intents, ["quiz"])

    def test_coordinated_negative_requests_do_not_trigger_reports(self):
        plan = TutorPlan("理解", [], "推荐", [{"tool": "lecture"}, {"tool": "assessment"}, {"tool": "path"}])
        self.assertEqual(_respect_requested_products(plan, "讲解水循环，不用出题，也不要学习路径或评估。", []).intents, ["lecture"])

    def test_positive_request_after_negation_is_preserved(self):
        plan = TutorPlan("理解", [], "推荐", [{"tool": "lecture"}, {"tool": "quiz"}])
        self.assertEqual(_respect_requested_products(plan, "不要出题只要讲解", []).intents, ["lecture"])

    @patch("agents.tutor_agent.get_active_memory_text", return_value="")
    @patch("agents.tutor_agent.get_learning_signal_summary", return_value={})
    @patch("agents.tutor_agent._recent_records", return_value=[])
    @patch("agents.tutor_agent.get_mastery_state", return_value=[])
    @patch("agents.tutor_agent.chat", side_effect=RuntimeError("provider down"))
    def test_model_failure_does_not_add_quizzes(self, *_):
        result = plan_tutor_actions("test", "月亮为什么有圆缺？", {})
        self.assertEqual(result.intents, ["lecture"])


if __name__ == "__main__":
    unittest.main()
