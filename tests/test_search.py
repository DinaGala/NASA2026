import unittest

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.database import engine

# Each question must find one of its investigations in the top 5 hits.
QUESTIONS = {
    "oxygen effect on flame spread over solids": {"PSI-25", "PSI-26"},
    "CO2 dilution putting out droplet flames": {"PSI-69"},
    "smoke detector response": {"PSI-102", "PSI-101"},
    "cool flames": {"PSI-39"},
    "large-scale flame spread in a spacecraft": {"PSI-98", "PSI-99", "PSI-100"},
}


def chunks_loaded() -> bool:
    try:
        with engine.connect() as connection:
            return bool(connection.execute(text("SELECT count(*) FROM psi_dashboard.chunks")).scalar())
    except OperationalError:
        return False


@unittest.skipUnless(chunks_loaded(), "psi_dashboard.chunks is empty or the database is down")
class SearchPapersTests(unittest.TestCase):
    def test_each_question_finds_its_investigation_in_the_top_5(self):
        from src.agent.search import search_papers

        for question, expected in QUESTIONS.items():
            with self.subTest(question=question):
                found = {hit.psi_id for hit in search_papers(question, k=5)}
                self.assertTrue(found & expected, f"top 5 came from {sorted(found)}")

    def test_psi_ids_filter_limits_the_hits(self):
        from src.agent.search import search_papers

        hits = search_papers("flame extinction", psi_ids=["PSI-69"], k=3)
        self.assertEqual(len(hits), 3)
        self.assertEqual({hit.psi_id for hit in hits}, {"PSI-69"})


if __name__ == "__main__":
    unittest.main()
