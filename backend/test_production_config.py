import unittest
from production_config import validate_production_config


class ProductionConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = {"ENVIRONMENT": "production", "MONGO_URL": "mongodb://localhost", "DB_NAME": "test",
                    "JWT_SECRET": "x" * 40, "FRONTEND_URL": "https://admin.example.test",
                    "POS_CORE_API_BASE_URL": "https://pos.example.test", "POS_CORE_API_KEY": "test-unique-key-" * 3}

    def test_complete_configuration(self):
        validate_production_config(self.env)

    def test_template_secrets_are_rejected(self):
        self.env['JWT_SECRET'] = 'REPLACE_WITH_A_RANDOM_SECRET_AT_LEAST_32_CHARACTERS'
        with self.assertRaisesRegex(RuntimeError, 'placeholder'):
            validate_production_config(self.env)

    def test_localhost_cannot_be_a_production_pos_destination(self):
        self.env['POS_CORE_API_BASE_URL'] = 'https://localhost:4001'
        with self.assertRaisesRegex(RuntimeError, 'localhost'):
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


class CorsRegexTests(unittest.TestCase):
    BASE = {"ENVIRONMENT": "production", "MONGO_URL": "m", "DB_NAME": "d", "JWT_SECRET": "x" * 40,
            "FRONTEND_URL": "https://admin.example.com", "POS_CORE_API_BASE_URL": "https://pos.example.com", "POS_CORE_API_KEY": "k" * 40}

    def test_shared_hosting_wildcards_are_rejected(self):
        for regex in (r"https://.*\.vercel\.app", ".*", r"https://.*\.onrender\.com"):
            result = validate_production_config({**self.BASE, "CORS_ORIGIN_REGEX": regex}, raise_on_error=False)
            self.assertFalse(result["ok"], regex)

    def test_absent_regex_is_fine(self):
        self.assertTrue(validate_production_config(self.BASE, raise_on_error=False)["ok"])
