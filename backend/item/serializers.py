from rest_framework import serializers
from .models import Item


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
