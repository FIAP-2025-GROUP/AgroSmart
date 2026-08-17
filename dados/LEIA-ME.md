# Imagens do projeto

Duas pastas, propositalmente separadas — a comparação entre elas é o resultado
central do relatório técnico.

## `exemplos/` — padrão PlantVillage

12 imagens, uma por classe, retiradas do próprio dataset. Servem para demonstrar
o app funcionando no cenário para o qual foi treinado e para a demonstração em
vídeo.

Como obter: no notebook do Colab, após montar os pipelines, salve uma imagem de
cada classe do conjunto de teste. Nomeie com a classe para facilitar a
conferência, por exemplo `tomate_requeima.jpg`, `uva_saudavel.jpg`.

## `campo/` — fotos reais de celular

10 a 15 fotos tiradas pela equipe, de plantas reais, **com o diagnóstico
verdadeiro conhecido** (anote em uma planilha à parte). Este é o conjunto que
mede a generalização de verdade.

Recomendações:

- Culturas suportadas: tomate, batata, uva, milho ou maçã.
- Uma folha por foto, preenchendo boa parte do quadro.
- Luz natural difusa; evite sol direto e sombra dura.
- Fotografe também folhas sadias, não só doentes.
- Inclua **uma foto de roseira ou outra planta não suportada**: ela deve cair em
  *indeterminado*, o que comprova o funcionamento do limiar de confiança.

Para medir a acurácia neste conjunto:

```bash
.venv\Scripts\python.exe classificar_pasta.py dados/campo
```

Compare o CSV gerado com os diagnósticos verdadeiros anotados e leve as duas
acurácias (PlantVillage × campo) para o relatório.
