"""Model selection and shared accuracy contracts; no live provider calls."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from agents import generation_quality, ppt_agent, interactive_agent, lecture_agent
from services import llm_client


class GenerationQualityTests(unittest.TestCase):
    def test_thinking_only_for_explicitly_supported_configured_models(self):
        for model in ('deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-pro', 'DEEPSEEK-FLASH'):
            with self.subTest(model=model), patch.object(generation_quality, 'get_default_model', return_value=model):
                self.assertEqual(generation_quality.generation_thinking(), 'enabled')
        for model in ('gpt-4o', 'claude-sonnet-4', 'deepseek-chat', 'deepseek-reasoner', 'custom/deepseek-flash', ''):
            with self.subTest(model=model), patch.object(generation_quality, 'get_default_model', return_value=model):
                self.assertIsNone(generation_quality.generation_thinking())

    def test_html_prompts_share_accuracy_runtime_and_interaction_contracts(self):
        html = '<!doctype html><html><head><title>Test</title></head><body><button>Test</button><script>let ready=true;</script></body></html>'
        with patch.object(interactive_agent, 'generation_thinking', return_value=None), patch.object(interactive_agent, 'chat_with_diagnostics', return_value=(html, {'finish_reason': 'stop'})) as provider:
            interactive_agent.generate_interactive({}, '', 'test')
        provider.assert_called_once()
        interactive_prompt = provider.call_args.args[0][0]['content']
        self.assertIsNone(provider.call_args.kwargs['thinking'])
        self.assertEqual(provider.call_args.kwargs['max_tokens'], 393216)
        for agent, prompt in ((ppt_agent, ppt_agent.build_system_prompt(ppt_agent.DESIGN_STYLES[0])), (interactive_agent, interactive_prompt)):
            with self.subTest(agent=agent.__name__):
                for contract in (generation_quality.CONTENT_ACCURACY, generation_quality.VISUAL_ACCURACY,
                                 generation_quality.HTML_RUNTIME):
                    self.assertIn(contract, prompt)
        self.assertIn(generation_quality.INTERACTION_ACCURACY, interactive_prompt)
        self.assertIn(generation_quality.CONTENT_ACCURACY, lecture_agent.SYSTEM_PROMPT)

    def test_vendor_switch_is_omitted_at_sdk_boundary_for_other_models(self):
        from types import SimpleNamespace
        completion = SimpleNamespace(model='test-model', usage=None, choices=[SimpleNamespace(
            finish_reason='stop', message=SimpleNamespace(content='ok'))])
        for thinking in (None, 'enabled'):
            with self.subTest(thinking=thinking), patch.object(llm_client, 'get_client') as client, patch.object(llm_client, 'record_token_usage'):
                client.return_value.chat.completions.create.return_value = completion
                llm_client.chat_with_diagnostics([{'role': 'user', 'content': 'test'}], model='test-model', thinking=thinking)
                kwargs = client.return_value.chat.completions.create.call_args.kwargs
                self.assertEqual(kwargs['max_tokens'], 393216)
                if thinking is None:
                    self.assertNotIn('extra_body', kwargs)
                else:
                    self.assertEqual(kwargs['extra_body'], {'thinking': {'type': 'enabled'}})


if __name__ == '__main__':
    unittest.main()
