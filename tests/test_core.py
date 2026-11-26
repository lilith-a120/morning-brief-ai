import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        import core
        self.core=core
        self.old=core.DB
        core.DB=Path(self.temp.name)/'test.db'
        core.init_db()
    def tearDown(self):
        self.core.DB=self.old
        self.temp.cleanup()
    def test_sample_save_and_search(self):
        self.assertEqual(self.core.load_demo(),3)
        self.assertEqual(self.core.load_demo(),0)
        self.assertTrue(self.core.retrieve('반도체 투자',self.core.list_articles())[0]['title'].find('반도체')>=0)
    def test_settings(self):
        self.core.set_setting('enabled','true')
        self.assertEqual(self.core.get_settings()['enabled'],'true')
    def test_no_key_evidence_mode(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):
            text,flag=self.core.ask_llm('AI 기사',self.core.list_articles())
            self.assertFalse(flag)
    def test_empty_retrieval(self):
        self.assertEqual(self.core.retrieve('질문',[]),[])
if __name__=='__main__': unittest.main()
