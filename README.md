# Veille Rungis

Veille quotidienne automatique : collecte (Bing/Google News + recherche web Claude), filtrage par sujet, images, page web.

## Mise en route
1. Créez un dépôt GitHub (privé) et envoyez tous ces fichiers.
2. Settings → Secrets and variables → Actions → New secret : `ANTHROPIC_API_KEY`.
3. Settings → Pages → Source : branche `main`, dossier `/ (root)`.
4. Onglet Actions → « Veille quotidienne » → Run workflow (premier test). Ensuite, exécution chaque jour vers 6h-7h.
5. Votre page : `https://<utilisateur>.github.io/<dépôt>/`

## Régler la veille
Tout se passe dans `config.yaml` : un sujet = des requêtes + un `brief` qui dit à Claude quoi garder et quoi rejeter.
Pour réduire le coût : `discovery_max_searches: 0` (flux d'actualités seuls) ou `min_score` plus haut.

## Limites
Facebook, Instagram, X et LinkedIn bloquent la collecte : seules leurs publications publiques indexées par le moteur de recherche remontent. Une vraie veille sociale exige des API payantes.
