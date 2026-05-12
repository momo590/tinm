# Substrat computationnel de TINM (Turtle-Inspired Navigation Memory)

> Base technique stable pour le papier de recherche.
> Compagnon du manifeste scientifique (`tnim_manifesto_scientific.md`).

---

## 1. Objets formels

### 1.1 Espace d'information

Graphe dynamique évolutif :

$$G_t = (V_t, E_t)$$

- $V_t$ : unités d'information (mail, paragraphe, message, issue, fichier...)
- $E_t \subseteq V \times V \times \mathcal{R}$ : arêtes typées (lien explicite, co-occurrence sémantique, même thread, même auteur, même projet)
- $\varphi : V \to \mathbb{R}^d$ : embedding multi-échelle, $\varphi = (\varphi_1, ..., \varphi_K)$ pour $K$ échelles de granularité

### 1.2 État de l'agent

$$s_t = (p_t, g_t, h_t, R_t)$$

- $p_t \in \Delta(V)$ : distribution de focus sur les nœuds (position probabiliste)
- $g_t \in \mathbb{R}^d$ : vecteur de but (encoding tâche/intent courante)
- $h_t \in \mathbb{R}^m$ : état caché récurrent (intégration de chemin)
- $R_t : V \to \mathcal{L}$ : carte de résolution par région

### 1.3 Niveaux de résolution

$$\mathcal{L} = \{\text{dormant}, \text{présence}, \text{aperçu}, \text{activation}, \text{engagement}\}$$

Chaque niveau $\ell$ paramètre un triplet $(\beta_\ell, f_\ell, \lambda_\ell)$ : budget d'attention, fréquence de mise à jour, taux de decay.

---

## 2. Neural Potential Field (cœur navigation)

Le champ de potentiel apprend à associer chaque position (étant donné but et historique) à une énergie :

$$U_\theta : \mathbb{R}^d \times \mathbb{R}^d \times \mathbb{R}^m \to \mathbb{R}$$

$$U_\theta(\varphi(v), g_t, h_t) \in \mathbb{R}$$

**Action** (descente locale dans le graphe) :

$$a_t = \arg\min_{v' \in \mathcal{N}(v_t)} U_\theta(\varphi(v'), g_t, h_t)$$

ou version continue (déplacement latent) :

$$v_{t+1} \approx v_t - \eta \cdot \nabla_\varphi U_\theta(\varphi(v_t), g_t, h_t)$$

### 2.1 Décomposition "toolbox" (turtle inspired)

$$U_\theta = \sum_{k} w_k(s_t) \cdot U_\theta^{(k)}$$

| Tête | Rôle biologique | Rôle TINM |
|---|---|---|
| $U^{(\text{magnetic})}$ | Signature géomagnétique | Attraction vers signatures globales (projet imprinté) |
| $U^{(\text{olfactif})}$ | Indices locaux récents | Attraction vers contexte de focus immédiat |
| $U^{(\text{courant})}$ | Suivre courants océaniques | Attraction induite par la SR (chemins fréquentés) |
| $U^{(\text{répulsif})}$ | Évitement | Pénalité redondance, hors-scope, bruit |

$w_k(s_t)$ est un **gating contextuel** appris : quelle stratégie domine selon la phase de navigation. Permet l'interprétabilité par stratification (loggable nativement).

---

## 3. Successor Representation multi-échelle

Pour chaque échelle $k$ avec horizon $\gamma_k$ :

$$M_k(v, v') = \mathbb{E}\left[\sum_{t \geq 0} \gamma_k^t \, \mathbb{1}\{V_t = v'\} \,\Big|\, V_0 = v, \pi_\theta\right]$$

- $\gamma_1$ petit → SR locale (voisinage immédiat, comme indices olfactifs)
- $\gamma_K$ grand → SR globale (zones d'attraction macro, comme champ magnétique)

**Mise à jour TD** :

$$M_k(v_t, \cdot) \leftarrow M_k(v_t, \cdot) + \alpha_k \left[ \mathbb{1}_{v_{t+1}} + \gamma_k M_k(v_{t+1}, \cdot) - M_k(v_t, \cdot) \right]$$

Avec $\alpha_K \ll \alpha_1$ (les couches globales apprennent lentement, comme le métabolisme tortue).

---

## 4. Couplage NPF ↔ SR (point clé)

Le NPF n'est pas indépendant de la SR. La tête "courant" est **dérivée** de la carte :

$$U_\theta^{(\text{courant})}(v, g) = -\log \sum_{v'} M_k(v, v') \cdot \text{sim}(\varphi(v'), g)$$

**Interprétation** : plus la SR prédit que je passerai par des nœuds proches du but, plus le potentiel à $v$ est attractif.

> **SR = carte des futurs ; NPF = force de descente sur cette carte.**

Les autres têtes ($U^{(\text{magnetic})}$, $U^{(\text{olfactif})}$, $U^{(\text{répulsif})}$) ne dépendent pas de la SR — elles fournissent les biais structurels (imprinting, focus, anti-bruit).

---

## 5. Désapprentissage formel

Chaque poids $w_i$ (entrée SR ou paramètre d'attention) est associé à un timestamp $\tau_i$ de dernière activation :

$$w_i(t) = w_i(\tau_i) \cdot \exp\left(-\lambda_{\ell(i)} \cdot (t - \tau_i)\right)$$

avec $\lambda_\ell$ croissant à mesure que $\ell$ descend :

| Niveau | $\lambda$ approximatif | Demi-vie |
|---|---|---|
| engagement | $\sim 0$ | ∞ tant qu'activé |
| activation | $\sim 10^{-2}/\text{min}$ | ~heure |
| aperçu | $\sim 10^{-2}/\text{heure}$ | ~jour |
| présence | $\sim 10^{-2}/\text{jour}$ | ~semaine |
| dormant | seuil de purge | $w \to 0$ |

Quand $w_i < \epsilon$, l'entrée est purgée. C'est **l'imprinting + recalage local** des tortues : signatures globales stables (faible $\lambda$ aux échelles macro) + détails locaux rapides à oublier (fort $\lambda$ aux échelles micro).

---

## 6. Intégration de chemin

$$h_t = \text{RNN}_\psi(h_{t-1}, \varphi(v_t), a_{t-1})$$

L'état récurrent encode la trajectoire récente — analogue à la position estimée d'une tortue qui intègre son self-motion. Sert à :

- contextualiser le NPF ($h_t$ entre dans $U_\theta$)
- détecter les cycles (retour répété à une zone = friction)
- alimenter le compresseur d'état latent

---

## 7. État latent compact (compresseur)

$$z_t = \text{Enc}_\phi(s_t) \in \mathbb{R}^c, \quad c \ll d$$

Le compresseur est entraîné à préserver :

- la position approximative ($p_t$)
- la trajectoire compressée ($h_t$)
- le top-$K$ des attracteurs actifs (nœuds à $U$ bas)
- le niveau de friction
- la carte de résolution $R_t$

**$z_t$ est l'objet partageable via PCP** (handoff parent/enfant, transfert inter-session). Il ne contient pas de contenu brut — c'est une signature d'état.

---

## 8. Le "next step utile" — définition opérationnelle

$$\text{NextStep}(s_t) = \arg\max_a \left[ Q_\theta(s_t, a) - \beta \cdot \text{cost}(a) \right]$$

avec $Q_\theta$ apprise par TD :

$$Q_\theta(s, a) \leftarrow Q_\theta(s, a) + \alpha \left[ r_t + \gamma \max_{a'} Q_\theta(s', a') - Q_\theta(s, a) \right]$$

Récompense composée :

$$r_t = r_{\text{user}} + r_{\text{progress}} + r_{\text{efficiency}} - r_{\text{friction}}$$

- $r_{\text{user}}$ : acceptation/rejet explicite de la suggestion
- $r_{\text{progress}}$ : réduction d'incertitude sur le but (mesurée par entropie de $p_t$ projetée sur $g_t$)
- $r_{\text{efficiency}}$ : pénalité tokens et temps
- $r_{\text{friction}}$ : hésitation, blocage, rejet implicite

Action $a$ : focus sur nœud, suggestion de texte, ouverture de fichier, spawn d'un sous-agent...

---

## 9. Détection de friction

$$\text{Friction}(s_t) = \alpha_1 \cdot \text{cycle}(h_t) + \alpha_2 \cdot \text{idle} + \alpha_3 \cdot \text{switch\_rate} + \alpha_4 \cdot \text{rejection\_rate}$$

Si $\text{Friction} > \tau_{\text{f}}$ → bascule de mode : proposer une pause, changer de stratégie (changer le gating $w_k$), ou demander explicitement à l'utilisateur.

---

## 10. Mapping biomimétique → mécanismes computationnels

| Attribut tortue | Mécanisme TINM précis |
|---|---|
| Imprinting géomagnétique | Signatures globales : $\gamma_K$ grand, $\lambda_K$ très petit dans $M_K$ |
| Navigation multi-échelle | $K$ échelles de SR + gating $w_k(s_t)$ |
| Toolbox stratégies | $U_\theta = \sum w_k U^{(k)}$ avec heads spécialisées |
| Intégration de chemin | RNN sur $h_t$ |
| Métabolisme lent | Learning rate hiérarchique : $\alpha_K \ll \alpha_1$ |
| Carapace | Gradient clipping + détection de drift distributionnel + rollback de policy |
| "Peu mais longtemps" | Promotion vers mémoire stable (Stipple) seulement après preuves répétées |

---

## 11. Hypothèses falsifiables (programme expérimental)

**H1 — Compression.** Pour une qualité de réponse $q$ fixée, TINM utilise $k$ fois moins de tokens injectés qu'un baseline RAG sur tâches de continuité ($k > 1$).

**H2 — Reprise.** Après interruption de durée $T$, TINM reprend la tâche avec moins de tokens d'amorçage qu'un baseline.

**H3 — Désapprentissage utile.** Désactiver le decay ($\lambda = 0$) dégrade la performance en environnement non-stationnaire ; un decay uniforme trop fort la dégrade en environnement stable. Il existe un profil $\{\lambda_\ell\}$ hiérarchique optimal.

**H4 — Spécialisation émergente.** Les têtes $U^{(k)}$ du NPF se spécialisent automatiquement par phase (analyse statistique des $w_k(s_t)$ sur trajectoires longues).

**H5 — Friction prédictive.** $\text{Friction}(s_t)$ prédit les abandons de tâche avec un lead-time mesurable avant l'événement.

---

## 12. Différenciation

| Système | Nature de la mémoire | Mécanisme central |
|---|---|---|
| RAG | Index documentaire | Query → top-$k$ similarité |
| World Models (Dreamer) | Dynamique latente | Imagination dans le latent |
| SR pur | Carte prédictive plate | TD updates sur un seul $\gamma$ |
| **TINM** | **SR multi-échelle + NPF + decay** | **Navigation par gradient sur carte décroissante** |

L'originalité TINM tient en trois jonctions :

1. **NPF ↔ SR** : la force de navigation est dérivée de la carte des futurs
2. **Decay hiérarchique** aligné sur la hiérarchie de résolution
3. **Décomposition toolbox** pour interprétabilité native

---

## 13. Questions ouvertes

- **Architecture de $U_\theta$** : attention sur voisins ($\sim$ GAT), MLP, ou hybride ?
- **Approximation de $M_k$** : TD-learning vs calcul matriciel exact (résolvante $(I - \gamma P)^{-1}$) — trade-off précision/coût.
- **Bootstrapping** : comment initialiser SR et NPF sur un océan vierge (cold start) ?
- **Stabilité du gating $w_k$** : risque d'oscillation entre stratégies — faut-il un mécanisme d'hystérésis ?
- **Métrique sans utilisateur réel** : pour itérer en labo, il faut un benchmark synthétique de continuité.
- **Spawn de sous-agents** : à partir de quel $z_t$ déléguer ? Critère exact ?
