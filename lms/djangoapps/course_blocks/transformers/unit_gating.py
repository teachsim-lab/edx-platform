"""
Unit Gating Transformer implementation.
Adds unit-level gating information to course blocks.
"""

from common.djangoapps.util import milestones_helpers
from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from opaque_keys.edx.keys import UsageKey


class UnitGatingTransformer(BlockStructureTransformer):
    """
    A transformer that adds unit-level gating information to vertical blocks.
    
    This transformer checks if unit gating is enabled for the course and
    adds gating information to vertical blocks.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class;
        same identifier used in setup.py.
        """
        return "unit_gating"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        # Request the fields needed for unit gating
        block_structure.request_xblock_fields('group_access', 'display_name')

    def transform(self, usage_info, block_structure):
        """
        Transforms block structure to add unit gating information.
        """
        # Check if unit gating is enabled for the course
        course = block_structure.get_course()
        if not getattr(course, 'enable_unit_gating', False):
            return

        # Process each block in the structure
        for block_key in block_structure:
            block = block_structure.get_xblock(block_key)
            if block and block.location.block_type == 'vertical':
                # Get unit gating information for this vertical block
                user = usage_info.user
                gated_content = self._get_unit_gated_content_info(block, user, course)
                
                if gated_content:
                    block_structure.override_xblock_field(block_key, 'gatedContent', gated_content)

    def _get_unit_gated_content_info(self, block, user, course):
        """
        Get unit gating information for a vertical block.
        
        This mirrors the logic from vertical_block.py._get_unit_gated_content_info
        """
        if not getattr(course, 'enable_unit_gating', False):
            return None

        # Get gating milestone for this block
        gating_namespace = f"{block.location}.gating"
        try:
            # Check if user has fulfilled the required milestone
            user_milestones = milestones_helpers.get_user_milestones(user.id, course.id)
            
            # Get the prerequisite milestone for this block
            prereq_milestone = milestones_helpers.get_course_content_milestones(
                course.id, 
                content_key=block.location,
                relationship='requires'
            )
            
            if not prereq_milestone:
                # No prerequisites set for this unit
                return None
                
            # Check if user has fulfilled the prerequisite
            prereq_milestone_id = prereq_milestone[0]['milestone_id']
            user_fulfilled = any(
                milestone['milestone_id'] == prereq_milestone_id and milestone['fulfilled']
                for milestone in user_milestones
            )
            
            gated_content = {
                'prereq_id': None,
                'prereq_url': None,
                'prereq_section_name': None,
                'gated_section_name': block.display_name,
            }
            
            if not user_fulfilled:
                gated_content['gated'] = True
                # Get prerequisite info for navigation
                fulfills_milestones = milestones_helpers.get_course_content_milestones(
                    course.id,
                    relationship='fulfills'
                )
                for milestone in fulfills_milestones:
                    if milestone['milestone_id'] == prereq_milestone_id:
                        # Find the prerequisite block
                        prereq_content_key = milestone.get('content_key')
                        if prereq_content_key:
                            prereq_usage_key = UsageKey.from_string(prereq_content_key)
                            try:
                                prereq_block = block.runtime.modulestore.get_item(prereq_usage_key)
                                if prereq_block:
                                    gated_content.update({
                                        'prereq_id': str(prereq_block.location),
                                        'prereq_url': f"/course/{course.id}/jump_to/{prereq_block.location}",
                                        'prereq_section_name': prereq_block.display_name,
                                    })
                            except Exception:
                                pass
                        break
            else:
                gated_content['gated'] = False
                
            return gated_content
            
        except Exception:
            # If anything fails, don't gate the content
            return None
