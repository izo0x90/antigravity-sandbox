from .constants import slugify_project_name


class SandboxNamingResolver:
    """
    Decouples infrastructure string formatting from domain configuration models.
    """

    @staticmethod
    def resolve_sandbox_name(project_name: str, profile: str = "default") -> str:
        clean_project = slugify_project_name(project_name)
        clean_profile = slugify_project_name(profile)
        if clean_profile == "default":
            return f"agy-sandbox-{clean_project}"
        return f"agy-sandbox-{clean_project}--{clean_profile}"

    @staticmethod
    def resolve_legacy_sandbox_name(project_name: str) -> str:
        return f"agy-sandbox-{project_name}".replace("_", "-")

    @staticmethod
    def resolve_container_name(project_name: str, profile: str = "default") -> str:
        clean_project = slugify_project_name(project_name)
        clean_profile = slugify_project_name(profile)
        if clean_profile == "default":
            return f"agy-sandbox-container-{clean_project}"
        return f"agy-sandbox-container-{clean_project}--{clean_profile}"

    @staticmethod
    def resolve_image_name(project_name: str) -> str:
        return f"agy-sandbox-{slugify_project_name(project_name)}"
