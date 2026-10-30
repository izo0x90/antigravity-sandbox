from .constants import slugify_project_name


def sandbox_name(project_name: str, profile: str = "default") -> str:
    project = slugify_project_name(project_name)
    prof = slugify_project_name(profile)
    if prof == "default":
        return f"agy-sandbox-{project}"
    return f"agy-sandbox-{project}--{prof}"


def image_name(project_name: str) -> str:
    return f"agy-sandbox-{slugify_project_name(project_name)}"
