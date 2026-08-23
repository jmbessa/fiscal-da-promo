class SourceError(Exception):
    """Falha ao consultar uma fonte de ofertas ou gerar link de afiliado."""


class ValidationError(Exception):
    """Post reprovado em um portão de validação pré-publicação."""
