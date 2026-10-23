# Homologacao de escrita do Google Ads MCP

O MCP de producao deve continuar com GOOGLE_ADS_MCP_WRITES_ENABLED=false.

Novo portao de autorizacao: as operacoes reais exigem simultaneamente:
- GOOGLE_ADS_MCP_WRITES_ENABLED=true
- GOOGLE_ADS_MCP_ALLOWED_WRITE_CUSTOMER_IDS com IDs exatos de dez digitos, separados por virgula. Sem lista, nada e alterado.
- GOOGLE_ADS_MCP_TEST_ACCOUNTS_ONLY=true (valor padrao): verificacao via API que customer.test_account e verdadeiro.
- Para liberar producao, o operador precisaria explicitamente definir GOOGLE_ADS_MCP_TEST_ACCOUNTS_ONLY=false, alem das demais configuracoes. Nao realizar isso na homologacao.

As novas ferramentas ads_management exigem previa validate_only, token de uso unico vinculado ao bearer OAuth e expiracao de dez minutos. Uma renovacao do bearer pode invalidar a previa, exigindo uma nova. A confirmacao exibida no chat continua sendo responsabilidade do operador; o servidor nao verifica a aprovacao humana em si.

Contas de teste exigem MCC separado do MCC de producao:
https://developers.google.com/google-ads/api/docs/best-practices/test-accounts

Passos:
1. Criar MCC de teste com conta Google nao vinculada ao MCC de producao.
2. Criar cliente de teste dentro do MCC de teste.
3. Registrar os IDs de ambos.
4. Criar instancia separada de homologacao no Easypanel, com URL e pasta OAuth independentes da V3 de producao.
5. Autenticar conta Google com acesso ao MCC de teste e usar seu ID como login_customer_id.
6. Configurar a allowlist SOMENTE com o ID do cliente de teste.
7. Fazer preview de cada operacao e executa-la apos aprovacao explicita.
8. Ler o recurso via API e verificar estado. Desabilitar escritas ao finalizar.

Nao expor segredos OAuth, signing key, developer token nem tokens de aprovacao.
Restricao: planos de aprovacao residem em memoria por processo; mais de uma replica requer armazenamento transacional compartilhado.