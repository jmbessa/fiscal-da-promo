# Fiscal da Promo — Visão de Negócio

## O que é

Fiscal da Promo é uma operação de mídia de performance no varejo digital brasileiro:
um sistema autônomo que descobre, seleciona e divulga ofertas dos marketplaces
Shopee e Mercado Livre em canais próprios (Telegram, Instagram), monetizada por
comissão de afiliado sobre cada venda originada. Diferente das páginas de
promoção operadas manualmente, a curadoria é orientada a dados e cada oferta
publicada carrega prova de desconto real.

## Proposta de valor

- **Para o consumidor:** ofertas selecionadas em autocuidado, casa, saúde e
  esportes, com a garantia de que o "de/por" é verdadeiro — o preço é comparado
  ao histórico de até 12 meses antes de ser publicado. O selo "menor preço
  verificado" só aparece quando há evidência.
- **Para o negócio:** receita de comissão com custo marginal próximo de zero
  por post, porque descoberta, ranqueamento, redação, arte e publicação são
  automatizados de ponta a ponta.

## Como funciona

1. **Descoberta** — consulta contínua às APIs de afiliados, a cada 5 minutos
   (08h–23h), cruzando desconto, volume de vendas e taxa de comissão.
2. **Seleção** — ranking por valor esperado (comissão × preço × popularidade),
   reforçado por uma watchlist semanal de inteligência de mercado (categorias
   em crescimento, itens em aceleração, mínimas históricas de preço) e por um
   modelo de linguagem que garante variedade e apelo.
3. **Validação** — portões automáticos: link ativo no domínio correto, preço
   coerente, imagem íntegra, texto dentro dos limites. Nada é publicado sem
   passar por todos.
4. **Produção** — copy gerada por IA dentro de guardrails (a IA nunca escreve
   preço ou link), arte de story e feed renderizada por template com identidade
   visual consistente.
5. **Distribuição** — Telegram como motor de volume (**60 ofertas/dia**,
   somadas as duas lojas), Instagram como motor de audiência (feed automático
   via API oficial; stories **semi-automáticos** — a arte e o link chegam
   prontos ao chat de operações e o dono posta no app), com tetos diários por
   canal.
6. **Operação** — cada execução reporta publicados, descartados e alertas a um
   canal de operações; estado e histórico ficam versionados.

## Diferenciais

- **Prova, não promessa:** histórico de preço como critério editorial — o
  atributo que as páginas concorrentes não conseguem sustentar manualmente.
- **Seleção por retorno esperado:** prioriza o que vende e comissiona melhor,
  não apenas o maior desconto anunciado.
- **Inteligência de mercado semanal:** categorias e produtos escolhidos a partir
  de dados reais de vendas dos dois marketplaces, revisados toda semana.
- **Escala sem headcount:** cadência de dezenas de posts diários com um único
  operador dedicando minutos por dia.

## Modelo de receita e métricas

Receita = cliques × taxa de conversão × ticket médio × comissão. Indicadores
acompanhados: posts/dia, CTR por canal, conversão nos painéis de afiliado,
receita por post e por categoria, crescimento de audiência. As decisões de
categoria e cadência são recalibradas a partir desses números.

## Status e roadmap

- **Entregue:** pipeline completo Shopee → Telegram, ranking por valor
  esperado, watchlist semanal com selo de preço verificado, geração de artes,
  stories semi-automáticos e feed via API oficial (aguardando credenciais).
- **Próximos passos:** ativação em produção com credenciais reais; Mercado
  Livre como segunda fonte; automação total dos stories quando a conta tiver
  maturidade; WhatsApp como canal adicional; migração para servidor dedicado.
