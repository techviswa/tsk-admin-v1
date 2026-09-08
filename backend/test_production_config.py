import unittest
from production_config import validate_production_config


class ProductionConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = {"ENVIRONMENT": "production", "MONGO_URL": "mongodb://localhost", "DB_NAME": "test",
                    "JWT_SECRET": "x" * 40, "FRONTEND_URL": "https://admin.example.test",
                    "POS_CORE_API_BASE_URL": "https://pos.example.test", "POS_CORE_API_KEY": "test-unique-key"}

    def test_complete_configuration(self):
        validate_production_config(self.env)

    def test_missing_secret_does_not_generate_a_new_key_after_restart(self):
        self.env.pop("JWT_SECRET")
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET"):
            validate_production_config(self.env)

    def test_render_requires_configuration_without_explicit_environment(self):
        with self.assertRaises(RuntimeError):
            validate_production_config({"RENDER": "true"})

    def test_development_key_is_rejected(self):
        self.env["POS_CORE_API_KEY"] = "dev-admincore-pos-bridge-key"
        with self.assertRaises(RuntimeError):
            validate_production_config(self.env)

    def test_insecure_origin_is_rejected(self):
        self.env["FRONTEND_URL"] = "http://admin.example.test"
        with self.assertRaises(RuntimeError):
            validate_production_config(self.env)


if __name__ == "__main__":
    unittest.main()
