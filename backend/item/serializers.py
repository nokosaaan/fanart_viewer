from rest_framework import serializers
from .models import Item, CharacterGroup, CharacterAliasGroup


class ItemSerializer(serializers.ModelSerializer):
    # Expose a lightweight boolean so list endpoints can show whether
    # a preview exists without embedding the full binary data in every item.
    has_preview = serializers.SerializerMethodField()

    class Meta:
        model = Item
        # exclude binary field from normal serialized output (use detail `preview/` endpoint to fetch bytes)
        exclude = ('preview_data',)
        read_only_fields = ('preview_content_type', 'has_preview')

    def get_has_preview(self, obj):
        # Prefer to check PreviewImage related rows; fall back to legacy preview_data
        try:
            # use hasattr to avoid accidental DB hits if relation broken
            if hasattr(obj, 'preview_images'):
                try:
                    return obj.preview_images.exists()
                except Exception:
                    # if relation access fails, fall back
                    pass
            return bool(obj.preview_data)
        except Exception:
            return False


class PreviewSerializer(serializers.Serializer):
    status = serializers.CharField()


class CharacterGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = CharacterGroup
        fields = ('id', 'name', 'characters', 'titles', 'parent', 'created_at')

    def validate_parent(self, value):
        """`parent` is self-referential, so nothing at the DB level stops a
        group being its own parent or two groups pointing at each other —
        either would make the ancestor walk in views._match_tagger_
        characters loop forever without the defensive `seen` guard there,
        so reject both here instead of relying on that guard alone."""
        if value is None or self.instance is None:
            return value
        if value.pk == self.instance.pk:
            raise serializers.ValidationError('A group cannot be its own parent.')
        node, seen = value, set()
        while node is not None and node.pk not in seen:
            if node.pk == self.instance.pk:
                raise serializers.ValidationError('This would create a circular group hierarchy.')
            seen.add(node.pk)
            node = node.parent
        return value


class CharacterAliasGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = CharacterAliasGroup
        fields = ('id', 'characters', 'linked', 'created_at')

    def validate_characters(self, value):
        # Always stored sorted+deduped so exact-set matching elsewhere
        # (candidates' `reviewed` set, train_character_classifier's linked-
        # group lookup, views._expand_character_alias) is simple list
        # equality rather than needing to re-normalize on every read.
        names = sorted(set(value or []))
        if len(names) < 2:
            raise serializers.ValidationError('characters must have at least 2 distinct names.')
        return names
