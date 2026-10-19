"""Build image-prompt requests from the shared code-owned prompt file."""

from app.default_prompts.internal_catalog import prompt, render


def build_image_prompt_system_prompt(mode: str = "first_person", subject: str = "") -> str:
    mode = str(mode or "first_person").strip()
    subject = str(subject or "").strip()
    if mode == "closeup" and not subject:
        raise ValueError("主体不能为空。")

    if mode == "first_person":
        return prompt("image", "first_person")
    if mode == "closeup":
        return render("image", "closeup", subject=subject).strip()
    return prompt("image", "normal")
