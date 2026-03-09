from __future__ import annotations

import unittest

from src.conversation import ConversationManager, ConversationTurn


class TestConversationManager(unittest.TestCase):

    def test_no_history_not_followup(self):
        cm = ConversationManager()
        self.assertFalse(cm.is_followup("What is the average anxiety score?"))

    def test_short_question_is_followup(self):
        cm = ConversationManager()
        cm.add_turn(ConversationTurn(question="What is the addiction level by gender?", sql="SELECT ...", answer="..."))
        self.assertTrue(cm.is_followup("What about males?"))

    def test_pronoun_detected_as_followup(self):
        cm = ConversationManager()
        cm.add_turn(ConversationTurn(question="Show top 5 age groups", sql="SELECT ...", answer="..."))
        self.assertTrue(cm.is_followup("Can you explain that in more detail?"))

    def test_keyword_detected_as_followup(self):
        cm = ConversationManager()
        cm.add_turn(ConversationTurn(question="Show addiction by gender", sql="SELECT ...", answer="..."))
        self.assertTrue(cm.is_followup("Now sort by anxiety score instead"))

    def test_standalone_question_not_followup(self):
        cm = ConversationManager()
        cm.add_turn(ConversationTurn(question="Show addiction by gender", sql="SELECT ...", answer="..."))
        self.assertFalse(cm.is_followup("What is the average anxiety score for each age group in the dataset?"))

    def test_build_context_prompt_includes_prior(self):
        cm = ConversationManager()
        cm.add_turn(ConversationTurn(
            question="What is the addiction level by gender?",
            sql="SELECT gender, AVG(addiction_level) FROM gaming_mental_health GROUP BY gender",
            answer="Males: 4.2, Females: 3.8",
        ))
        prompt = cm.build_context_prompt("What about males specifically?")
        self.assertIn("Previous question", prompt)
        self.assertIn("Previous SQL", prompt)
        self.assertIn("Follow-up question", prompt)

    def test_max_turns_enforced(self):
        cm = ConversationManager(max_turns=3)
        for i in range(5):
            cm.add_turn(ConversationTurn(question=f"Question {i}"))
        self.assertEqual(len(cm.history), 3)
        self.assertEqual(cm.history[0].question, "Question 2")

    def test_clear(self):
        cm = ConversationManager()
        cm.add_turn(ConversationTurn(question="test"))
        cm.clear()
        self.assertEqual(len(cm.history), 0)


if __name__ == "__main__":
    unittest.main()
